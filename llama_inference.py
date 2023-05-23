import argparse
import time

import torch
import torch.nn as nn
import quant

from gptq import GPTQ
from utils import find_layers, DEV, set_seed, get_wikitext2, get_ptb, get_c4, get_ptb_new, get_c4_new, get_loaders
import transformers
from transformers import AutoTokenizer


def get_llama(model):

    def skip(*args, **kwargs):
        pass

    torch.nn.init.kaiming_uniform_ = skip
    torch.nn.init.uniform_ = skip
    torch.nn.init.normal_ = skip
    from transformers import LlamaForCausalLM
    model = LlamaForCausalLM.from_pretrained(model, torch_dtype='auto')
    model.seqlen = 2048
    return model


def load_quant(model, checkpoint, wbits, groupsize=-1, fused_mlp=True, eval=True, warmup_autotune=True):
    from transformers import LlamaConfig, LlamaForCausalLM
    config = LlamaConfig.from_pretrained(model)

    def noop(*args, **kwargs):
        pass

    torch.nn.init.kaiming_uniform_ = noop
    torch.nn.init.uniform_ = noop
    torch.nn.init.normal_ = noop

    torch.set_default_dtype(torch.half)
    transformers.modeling_utils._init_weights = False
    torch.set_default_dtype(torch.half)
    model = LlamaForCausalLM(config)
    torch.set_default_dtype(torch.float)
    if eval:
        model = model.eval()
    layers = find_layers(model)
    for name in ['lm_head']:
        if name in layers:
            del layers[name]
    quant.make_quant_linear(model, layers, wbits, groupsize)

    del layers

    print('Loading model ...')
    if checkpoint.endswith('.safetensors'):
        from safetensors.torch import load_file as safe_load
        model.load_state_dict(safe_load(checkpoint), strict=False)
    else:
        model.load_state_dict(torch.load(checkpoint), strict=False)

    if eval:
        quant.make_quant_attn(model)
        quant.make_quant_norm(model)
        if fused_mlp:
            quant.make_fused_mlp(model)
    if warmup_autotune:
        quant.autotune_warmup_linear(model, transpose=not (eval))
        if eval and fused_mlp:
            quant.autotune_warmup_fused(model)
    model.seqlen = 2048
    print('Done.')

    return model

class LLM:

    def __init__(
        self,
        model_path: str, # /dir/alpaca-native-4bit
        load_path: str, # /dir/alpaca-native-4bit/alpaca7b-4bit.pt
        wbits: str = 4,
        groupsize: str = 128,
        top_p: float = 0.95,
        temperature: float = 0.8,
        device: int = -1 # The device used to load the model when using safetensors. Default device is "cpu" or specify, 0,1,2,3,... for GPU device.
    ):

        self.model = load_quant(
            model=model_path,
            checkpoint=load_path,
            wbits=wbits,
            groupsize=groupsize
        ).to(DEV) # set to CUDA

        self.tokenizer = AutoTokenizer.from_pretrained(model_path, use_fast=False)

        self.top_p = top_p
        self.temperature = temperature
        self.device = device
    
    def generate(
        self,
        text: str,
        min_length: int = 10,
        max_length: int = 300,
    ) -> str:

        input_ids = self.tokenizer.encode(text, return_tensors="pt").to(DEV)

        with torch.no_grad(): # what does no grad do?
            generated_ids = self.model.generate(
                input_ids,
                do_sample=True,
                min_length=min_length,
                max_length=max_length,
                top_p=self.top_p,
                temperature=self.temperature,
            )
        
        start = time.time()
        output = self.tokenizer.decode([el.item() for el in generated_ids[0]])
        end = time.time()
        print(f'Detokenization took: {end - start} seconds.')

        return output




if __name__ == '__main__':

    parser = argparse.ArgumentParser()

    parser.add_argument('model', type=str, help='llama model to load')
    parser.add_argument('--wbits', type=int, default=16, choices=[2, 3, 4, 8, 16], help='#bits to use for quantization; use 16 for evaluating base model.')
    parser.add_argument('--groupsize', type=int, default=-1, help='Groupsize to use for quantization; default uses full row.')
    parser.add_argument('--load', type=str, default='', help='Load quantized model.')

    parser.add_argument('--text', type=str, help='input text')

    parser.add_argument('--min_length', type=int, default=10, help='The minimum length of the sequence to be generated.')

    parser.add_argument('--max_length', type=int, default=50, help='The maximum length of the sequence to be generated.')

    parser.add_argument('--top_p',
                        type=float,
                        default=0.95,
                        help='If set to float < 1, only the smallest set of most probable tokens with probabilities that add up to top_p or higher are kept for generation.')

    parser.add_argument('--temperature', type=float, default=0.8, help='The value used to module the next token probabilities.')

    parser.add_argument('--device', type=int, default=-1, help='The device used to load the model when using safetensors. Default device is "cpu" or specify, 0,1,2,3,... for GPU device.')

    args = parser.parse_args()

    if type(args.load) is not str:
        args.load = args.load.as_posix()

    if args.load:
        model = load_quant(args.model, args.load, args.wbits, args.groupsize)
    else:
        model = get_llama(args.model)
        model.eval()

    model.to(DEV)
    tokenizer = AutoTokenizer.from_pretrained(args.model, use_fast=False)
    input_ids = tokenizer.encode(args.text, return_tensors="pt").to(DEV)
    # question here

    with torch.no_grad():
        generated_ids = model.generate(
            input_ids,
            do_sample=True,
            min_length=args.min_length,
            max_length=args.max_length,
            top_p=args.top_p,
            temperature=args.temperature,
        )
    print(tokenizer.decode([el.item() for el in generated_ids[0]]))
