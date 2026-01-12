import torch
from transformers import AutoModelForCausalLM, AutoTokenizer



# model_path = modelpath

def modelrunner(mdlpath: str, prompt:str, data:str)->str:
    device = "cpu"  # Use CPU since CUDA is not available
    tokenizer = AutoTokenizer.from_pretrained(mdlpath)
    model = AutoModelForCausalLM.from_pretrained(mdlpath)
    model.eval()
    # results = str(data) +str(prompt).replace("<INSERT DOCUMENT TEXT HERE>",data)
    # results = """You are an invoice extraction engine.
    #              Use the following YAML schema and extract values from the document text.
    #              Return ONLY valid YAML.""" + prompt + results
    instruction_prompt = """You are an invoice extraction engine.
                Use the following YAML schema and extract values from the document text.
                Return ONLY valid YAML.
                """
    schema_prompt = prompt          # your YAML schema
    document_text = data    # OCR / extracted PDF text
    
    # final_prompt = (
    #     instruction_prompt
    #     + "\n\n### YAML SCHEMA\n"
    #     + schema_prompt
    #     + "\n\n### DOCUMENT TEXT\n"
    #     + document_text
    # )

    chat = [
    {
        "role": "system",
        "content": instruction_prompt
    },
    {
        "role": "user",
        "content": "YAML Schema:\n" + schema_prompt
    },
    {
        "role": "user",
        "content": "Invoice Text:\n" + document_text
    }
]

    chat_text = tokenizer.apply_chat_template(
        chat,
        tokenize=False,
        add_generation_prompt=True
    )

    input_tokens = tokenizer(chat_text, return_tensors="pt").to(device)

    output = model.generate(
        **input_tokens,
        max_new_tokens=500,
        do_sample=False
    )

    input_len = input_tokens["input_ids"].shape[1]
    generated_ids = output[0][input_len:]

    final_response = tokenizer.decode(
        generated_ids,
        skip_special_tokens=True
    ).strip()

    return final_response
