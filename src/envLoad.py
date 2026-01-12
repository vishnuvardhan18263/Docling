from dotenv import load_dotenv
import os

load_dotenv()  # loads .env into environment

srcpath = os.getenv("SRC_PATH")
srcfiles = os.getenv("SOURCE_FILES")
envpath = os.getenv("ENV_PATH")
modelpath = os.getenv("MODEL_PATH")

if __name__ == "__main__":
    pass
# print(f"srcpath: {srcpath}")
# print(f"srcfiles: {srcfiles}")
