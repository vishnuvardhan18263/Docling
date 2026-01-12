import yaml
# from envLoad import envpath
config = {}

def read_env_file(envpath: str) -> dict:
    """Reads the .env file and returns a dictionary of key-value pairs.

    Args:
        env_path (str): Path to the .env file.

    Returns:
        dict: Dictionary containing key-value pairs from the .env file.
        '<INSERT DOCUMENT TEXT HERE>'
    """

    promptTemplateFile = envpath + "PromptTemplate.yaml"
    with open(promptTemplateFile, 'r') as file:
        config = yaml.safe_load(file)
        config = str(config).replace('<INSERT DOCUMENT TEXT HERE>', "changed text")
        print(config)
    return config