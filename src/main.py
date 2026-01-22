from envLoad import srcpath, srcfiles, modelpath,envpath
from promptTemplateLoads import read_env_file
from IndiExtractor import ExtractInvData
from graniteModel import modelrunner
import sys
import os
sys.stdout.reconfigure(encoding='utf-8')
final_response = ""

for files in os.listdir(srcfiles):
    config = read_env_file(envpath)
    results = ExtractInvData(os.path.join(srcfiles,files))
    modelresponse  = modelrunner(modelpath, config, results)
    
    with open(os.path.join(srcfiles,files+"_ModelResponse.txt"), 'w', encoding='utf-8') as outFile:
        outFile.write(modelresponse)
        
    print(os.path.join(srcfiles,files))
    print(modelresponse)
    
