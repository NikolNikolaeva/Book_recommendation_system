"""Run with: python -m book_recsys"""

import uvicorn

if __name__ == "__main__":
    uvicorn.run("book_recsys.main:app", host="127.0.0.1", port=8000, reload=True)
