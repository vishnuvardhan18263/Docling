"""Reads the YAML Config files and provides access to the configuration values.
"""

from dotenv import load_dotenv
import os
import yaml

load_dotenv()  # loads .env into environment

srcpath = os.getenv("SRC_PATH")

def read_env_file(env_path: str) -> dict:
    """Reads the .env file and returns a dictionary of key-value pairs.

    Args:
        env_path (str): Path to the .env file.

    Returns:
        dict: Dictionary containing key-value pairs from the .env file.
        '<INSERT DOCUMENT TEXT HERE>'
    """
    config = {}
    promptTemplateFile = env_path + "PromptTemplate.yaml"
    with open(promptTemplateFile, 'r') as file:
        config = yaml.safe_load(file)
    return config

print(read_env_file(srcpath))