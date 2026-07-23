with open("scratch/runner_log_ok.txt", "r", encoding="utf-16") as f:
    lines = f.readlines()

in_step = False
for line in lines:
    if "Run Fetch Feux Script" in line:
        in_step = True
    elif "Commit and Push Updated Site" in line:
        in_step = False
    
    if in_step:
        print(line.strip())
