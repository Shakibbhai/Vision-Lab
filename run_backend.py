import sys
import traceback
import uvicorn

if __name__ == "__main__":
    try:
        uvicorn.run("app.main:app", host="0.0.0.0", port=8000, log_level="info")
    except Exception as e:
        with open("backend_crash.log", "w", encoding="utf-8") as f:
            f.write(f"Exception: {e}\n")
            traceback.print_exc(file=f)
        sys.exit(1)
