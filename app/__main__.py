import uvicorn

from app.config import get_settings

settings = get_settings()
uvicorn.run("app.main:app", host=settings.host, port=settings.port, reload=settings.is_development)
