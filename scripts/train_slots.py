"""LoRA for the form-filling step only: the prompt is the runner's own, and the loss is on the slots reply.

Labels come from scripts/build_labels.py (odd train shops). A few questions are held back to watch the loss, grouped by
question so a rewrite never sits on the other side. Rank 16 on every linear layer adds about 64M parameters to
Qwen3-14B's 14.77B, inside the challenge's 15B total; a larger rank would not fit."""
import argparse
import json
import math
from pathlib import Path
import random
import time

import torch
from peft import LoraConfig, get_peft_model
from transformers import AutoModelForCausalLM, AutoTokenizer

TARGETS = ['q_proj', 'k_proj', 'v_proj', 'o_proj', 'gate_proj', 'up_proj', 'down_proj']
# Every linear layer of the language model, never of a vision or audio tower (Gemma 4 ships both in one checkpoint).
TARGET_PATTERN = r'^(?!.*(?:vision|audio|multi_modal|embed_)).*\.(?:' + '|'.join(TARGETS) + r')$'


def end_of_reply(tokenizer, messages):
    """What the chat template writes after an assistant reply ("<|im_end|>" for Qwen3), so the model learns to stop."""
    content = messages[2]['content']
    try:
        full = tokenizer.apply_chat_template(messages[:3], tokenize=False, enable_thinking=False)
        return full[full.rindex(content) + len(content):].rstrip() or tokenizer.eos_token
    except ValueError:
        return tokenizer.eos_token


def encode(tokenizer, example, limit):
    """Prompt tokens as llama.cpp renders them with thinking off, then the reply; only the reply is learned."""
    messages = example['messages']
    prompt = tokenizer.apply_chat_template(messages[:2], tokenize=False, add_generation_prompt=True, enable_thinking=False)
    prompt_ids = tokenizer(prompt, add_special_tokens=False)['input_ids']
    reply_ids = tokenizer(messages[2]['content'] + end_of_reply(tokenizer, messages), add_special_tokens=False)['input_ids']
    ids = prompt_ids + reply_ids
    if len(ids) > limit:
        return None
    return torch.tensor([ids]), torch.tensor([[-100] * len(prompt_ids) + reply_ids])


def collate_batch(batch, pad_id):
    max_len = max(ids.shape[1] for ids, _ in batch)
    input_ids = torch.full((len(batch), max_len), pad_id, dtype=torch.long)
    attention_mask = torch.zeros((len(batch), max_len), dtype=torch.long)
    labels = torch.full((len(batch), max_len), -100, dtype=torch.long)
    for i, (ids, lab) in enumerate(batch):
        length = ids.shape[1]
        input_ids[i, :length] = ids[0]
        attention_mask[i, :length] = 1
        labels[i, :length] = lab[0]
    return input_ids, attention_mask, labels


def mean_loss(model, items, pad_id=0, batch_size=1):
    model.eval()
    losses = []
    with torch.no_grad():
        for start in range(0, len(items), batch_size):
            chunk = items[start:start + batch_size]
            ids, mask, lab = collate_batch(chunk, pad_id)
            loss = model(input_ids=ids.cuda(), attention_mask=mask.cuda(), labels=lab.cuda()).loss.item()
            losses.extend([loss] * len(chunk))
    model.train()
    return sum(losses) / len(losses) if losses else float('nan')


def save_checkpoint(model, optimizer, schedule, step, seen, folder, hook, batch_size=None):
    """Everything needed to go on after an interruption, written to a temporary folder and swapped in whole."""
    from peft import get_peft_model_state_dict
    from safetensors.torch import save_file
    import shutil
    import subprocess
    temporary = folder.with_name(folder.name + '.tmp')
    shutil.rmtree(temporary, ignore_errors=True)
    temporary.mkdir(parents=True)
    save_file({k: v.contiguous() for k, v in get_peft_model_state_dict(model).items()}, str(temporary / 'adapter_model.safetensors'))
    torch.save({'optimizer': optimizer.state_dict(), 'schedule': schedule.state_dict(), 'step': step, 'seen': seen, 'batch_size': batch_size}, temporary / 'state.pt')
    shutil.rmtree(folder, ignore_errors=True)
    temporary.rename(folder)
    print(f'checkpoint at step {step} -> {folder}', flush=True)
    if hook:
        subprocess.Popen(hook, shell=True)


def main(args):
    random.seed(2026)
    torch.manual_seed(2026)
    rows = [json.loads(line) for line in open(args.data)]
    if args.leave_out:
        # Leave-one-shop-type-out: no label from shops of this type, so the fold measures a type the adapter never saw.
        types = {b: t.rstrip('?') for b, t in json.loads((Path(__file__).resolve().parents[1] / 'splits' / 'shop_types.json').read_text()).items()}
        kept = [row for row in rows if types.get(row['business_id']) != args.leave_out]
        print(f'leave out {args.leave_out}: {len(rows) - len(kept)} of {len(rows)} labels dropped', flush=True)
        rows = kept
    questions = sorted({row['id'] for row in rows})
    random.shuffle(questions)
    held = set(questions[:max(1, int(len(questions) * args.held))])
    tokenizer = AutoTokenizer.from_pretrained(args.model)
    pad_id = tokenizer.pad_token_id if tokenizer.pad_token_id is not None else tokenizer.eos_token_id
    train = [item for row in rows if row['id'] not in held for item in [encode(tokenizer, row, args.max_length)] if item]
    valid = [item for row in rows if row['id'] in held for item in [encode(tokenizer, row, args.max_length)] if item]
    vram_gb = torch.cuda.get_device_properties(0).total_memory / (1024 ** 3) if torch.cuda.is_available() else 0.0
    batch_size = args.batch_size if args.batch_size > 0 else (4 if vram_gb >= 70 else 1)
    eff_accum = max(1, args.accum // batch_size)
    # Measured on a 96 GB RTX Pro 6000: batch 4 without checkpointing runs out of memory (~93 GB at step 1);
    # with checkpointing it peaks near 49 GB, so only far larger cards skip it.
    use_ckpt = args.grad_checkpoint if args.grad_checkpoint is not None else (vram_gb < 140)
    print(f'{len(train)} train and {len(valid)} held-back examples; longest {max(ids.shape[1] for ids, _ in train + valid)} tokens; '
          f'VRAM={vram_gb:.1f}GB batch_size={batch_size} accum={eff_accum} grad_ckpt={use_ckpt}', flush=True)
    try:
        model = AutoModelForCausalLM.from_pretrained(args.model, torch_dtype=torch.bfloat16, device_map={'': 0}, attn_implementation='sdpa')
    except (ValueError, KeyError):
        # A checkpoint that also holds vision or audio towers (Gemma 4) loads through its image-text class; only text is trained.
        from transformers import AutoModelForImageTextToText
        model = AutoModelForImageTextToText.from_pretrained(args.model, torch_dtype=torch.bfloat16, device_map={'': 0}, attn_implementation='sdpa')
    if use_ckpt:
        model.gradient_checkpointing_enable(gradient_checkpointing_kwargs={'use_reentrant': False})
        model.enable_input_require_grads()
    model = get_peft_model(model, LoraConfig(r=args.rank, lora_alpha=2 * args.rank, lora_dropout=0.05, target_modules=TARGET_PATTERN, task_type='CAUSAL_LM'))
    model.print_trainable_parameters()
    trainable = [p for p in model.parameters() if p.requires_grad]
    optimizer = torch.optim.AdamW(trainable, lr=args.lr, weight_decay=0.0)
    micro_batches_per_epoch = math.ceil(len(train) / batch_size)
    steps = math.ceil(micro_batches_per_epoch * args.epochs / eff_accum) if not args.max_steps else args.max_steps
    warmup = min(10, steps // 10 + 1)
    schedule = torch.optim.lr_scheduler.LambdaLR(optimizer, lambda s: min(1.0, (s + 1) / warmup) * 0.5 * (1 + math.cos(math.pi * min(s, steps) / steps)))
    started, step, seen, running = time.monotonic(), 0, 0, 0.0
    checkpoint = Path(args.checkpoint) if args.checkpoint else None
    if checkpoint and (checkpoint / 'state.pt').exists():
        # Resume where an interrupted run stopped: the adapter, optimizer and schedule come back, and the
        # epoch orders below replay from the same seed, so the micro-batches already learned are skipped.
        from peft import set_peft_model_state_dict
        from safetensors.torch import load_file
        set_peft_model_state_dict(model, load_file(str(checkpoint / 'adapter_model.safetensors')))
        state = torch.load(checkpoint / 'state.pt', weights_only=False)
        optimizer.load_state_dict(state['optimizer'])
        schedule.load_state_dict(state['schedule'])
        step, seen = state['step'], state['seen']
        if state.get('batch_size', 4) != batch_size:
            # Another card, another micro-batch (checkpoints without the field came from batch 4): count the examples already learned instead of micro-batches.
            # The padding windows differ too, so a handful of examples near the cut may repeat or be skipped.
            seen = step * eff_accum
        print(f'resumed from {checkpoint} at step {step}', flush=True)
    else:
        print('held-back loss before training', round(mean_loss(model, valid, pad_id, batch_size), 4), flush=True)
    model.train()
    for epoch in range(math.ceil(args.epochs)):
        order = list(range(len(train)))
        random.shuffle(order)
        # Sort within small windows of 4 * batch_size to minimize padding overhead while preserving randomness.
        window = batch_size * 4
        for w in range(0, len(order), window):
            sub = order[w:w + window]
            sub.sort(key=lambda idx: train[idx][0].shape[1])
            order[w:w + window] = sub
        for b_start in range(0, len(order), batch_size):
            if epoch * micro_batches_per_epoch + b_start // batch_size < seen:
                continue
            batch_items = [train[idx] for idx in order[b_start:b_start + batch_size]]
            ids, mask, lab = collate_batch(batch_items, pad_id)
            loss = model(input_ids=ids.cuda(), attention_mask=mask.cuda(), labels=lab.cuda()).loss / eff_accum
            loss.backward()
            running += loss.item()
            seen += 1
            if seen % eff_accum:
                continue
            torch.nn.utils.clip_grad_norm_(trainable, 1.0)
            optimizer.step()
            schedule.step()
            optimizer.zero_grad(set_to_none=True)
            step += 1
            if step % 10 == 0 or step == steps:
                hours = (time.monotonic() - started) / 3600
                print(f'step {step}/{steps} loss {running / 10:.4f} lr {schedule.get_last_lr()[0]:.2e} {hours:.2f} h', flush=True)
                running = 0.0
            if checkpoint and args.save_every and step % args.save_every == 0 and step < steps:
                save_checkpoint(model, optimizer, schedule, step, seen, checkpoint, args.on_save, batch_size)
            # Stop on the step budget or the time budget, keeping what was learned.
            if step >= steps or (time.monotonic() - started) / 3600 > args.max_hours:
                break
        else:
            print(f'epoch {epoch + 1} held-back loss', round(mean_loss(model, valid, pad_id, batch_size), 4), flush=True)
            continue
        break
    print('held-back loss after training', round(mean_loss(model, valid, pad_id, batch_size), 4), flush=True)
    model.save_pretrained(args.output)
    print('saved', args.output, flush=True)


if __name__ == '__main__':
    parser = argparse.ArgumentParser()
    parser.add_argument('--data', default='results/training/slots-v3-all.jsonl')
    parser.add_argument('--model', required=True, help='Hugging Face weights of the base model')
    parser.add_argument('--output', required=True)
    parser.add_argument('--rank', type=int, default=16)
    parser.add_argument('--lr', type=float, default=1e-4)
    parser.add_argument('--epochs', type=float, default=2)
    parser.add_argument('--batch-size', type=int, default=0, help='micro-batch size (0 = auto 4 on >=70GB VRAM else 1)')
    parser.add_argument('--accum', type=int, default=16, help='target effective batch size (divided by batch_size)')
    parser.add_argument('--grad-checkpoint', dest='grad_checkpoint', action='store_true', default=None)
    parser.add_argument('--no-grad-checkpoint', dest='grad_checkpoint', action='store_false')
    parser.add_argument('--held', type=float, default=0.05)
    parser.add_argument('--max-length', type=int, default=4096)
    parser.add_argument('--max-steps', type=int, default=0, help='stop after this many optimizer steps (a smoke test)')
    parser.add_argument('--checkpoint', default='', help='folder to save to and resume from')
    parser.add_argument('--save-every', type=int, default=40, help='optimizer steps between checkpoints')
    parser.add_argument('--on-save', default='', help='shell command run in the background after each checkpoint (e.g. a copy to remote storage)')
    parser.add_argument('--max-hours', type=float, default=3.5)
    parser.add_argument('--leave-out', default='', help='shop type of splits/shop_types.json whose labels are dropped (a held-out fold)')
    main(parser.parse_args())
