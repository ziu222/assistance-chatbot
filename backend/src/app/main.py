from fastapi import FastAPI

app = FastAPI(title="assistance-chatbot")


@app.get("/health")
def health() -> dict[str, str]:
    return {"status": "ok"}
