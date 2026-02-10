
import sys
import os
from envLoad import srcpath, srcfiles, modelpath, envpath, ollamaUrl
from promptTemplateLoads import read_env_file
from IndiExtractor import ExtractInvData
from gemmModel import modelrunner
from datetime import datetime
# from graniteModel import modelrunner  # switch if needed

sys.stdout.reconfigure(encoding='utf-8')
start = datetime.now()

def main() -> int:
    # Load config ONCE (not per file)
    config = read_env_file(envpath)

    # Defensive checks
    if not os.path.isdir(srcfiles):
        print(f"[ERROR] Source folder not found: {srcfiles}", file=sys.stderr)
        return 2

    pdf_names = [f for f in os.listdir(srcfiles) if f.lower().endswith(".pdf")]
    if not pdf_names:
        print("[INFO] No PDF files found. Nothing to do.")
        return 0
    print(f"############################### [INFO] Processing {start}")
    
    for file in pdf_names:
        pdf_path = os.path.join(srcfiles, file)
        try:
            
            
            results = ExtractInvData(pdf_path)

            # If modelrunner is synchronous and returns text, this should complete.
            modelresponse = modelrunner(ollamaUrl, config, results)

            textfile = pdf_path + "_ModelResponse.txt"
            with open(textfile, 'w', encoding='utf-8') as outFile:
                outFile.write(modelresponse if modelresponse is not None else "")
                print(f"[INFO] Processing {datetime.now() - start}: {pdf_path}")
            print(f"[OK] Wrote: {textfile}")
        except KeyboardInterrupt:
            print("\n[WARN] Interrupted by user. Exiting ...")
            return 130
        except Exception as e:
            pass
            # Continue with other files; collect/log errors as needed
    print("[DONE] All PDFs processed.")
    print(f"############################### Completed: {datetime.now() - start}")
    return 0

if __name__ == "__main__":
    code = main()
    # Force clean exit; helpful if libraries left non-daemon threads around
    sys.exit(code)

