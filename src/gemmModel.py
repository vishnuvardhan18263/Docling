import torch
from transformers import AutoModelForCausalLM, AutoTokenizer
import re
# model_path = modelpath
result = ""
def modelrunner(mdlpath: str, prompt:str, data:str)->str:
    device = "cpu"  # Use CPU since CUDA is not available
    tokenizer = AutoTokenizer.from_pretrained(mdlpath)
    model = AutoModelForCausalLM.from_pretrained(mdlpath)
    model.eval()

    document_text = data 
    
    prompt = """You are a finance expert. Make sure to extract the following required data into a structured format:
    • Invoice Number
    • Company Code
    • Supplier
    • Reference Number
    • Invoice Date
    • Shipment Date
    • Currency Type
    • Tax Amount
    • Total Amount
    • AccountName
    • BankName
    • Address
    • AccountNumber
    • Sweift code/Swift code
    Note:
    > If the above fields are not available, see the respective linked data.
    > For example, Invoice Number can also be a PO number or just a number.
    """

    prompt += "\n\n" + document_text

    chat = [{"role": "user", "content": prompt}]

    chat_text = tokenizer.apply_chat_template(
        chat,
        tokenize=False,
        add_generation_prompt=True
    )

    inputs = tokenizer(chat_text, return_tensors="pt").to(device)

    with torch.no_grad():
        outputs = model.generate(
            **inputs,
            max_new_tokens=2000
        )

    generated_tokens = outputs[0][inputs["input_ids"].shape[-1]:]

    result = tokenizer.decode(
        generated_tokens,
        skip_special_tokens=True
    ).strip()
    print("MODEL OUTPUT:\n", result)
    return result