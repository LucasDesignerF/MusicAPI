from fastapi import FastAPI, HTTPException, Response, Request
from fastapi.responses import HTMLResponse, StreamingResponse
from fastapi.templating import Jinja2Templates
from pydantic import BaseModel
import yt_dlp
import asyncio
import re
import os
from colorama import Fore, Style, init
from datetime import datetime, timedelta
from dotenv import load_dotenv
import aiohttp
import logging

# Inicializar colorama
init(autoreset=True)

# Configurar logging
logging.basicConfig(level=logging.INFO, format='%(asctime)s - %(levelname)s - %(message)s')
logger = logging.getLogger(__name__)

# Carregar variáveis de ambiente
load_dotenv()
PORT = os.getenv("PORT", 8000)
API_BASE_URL = os.getenv("API_BASE_URL", f"http://localhost:{PORT}")

# Inicializar FastAPI
app = FastAPI(
    title="MusicAPI",
    description="API para bots de música no Discord, oferecendo busca e streaming de músicas do YouTube como alternativa ao yt-dlp.",
    version="1.0.0"
)

# Configurar Jinja2 templates
templates = Jinja2Templates(directory="templates")

# Configurações do yt-dlp
YDL_OPTS = {
    'format': 'bestaudio/best',
    'noplaylist': True,
    'quiet': True,
    'socket_timeout': 10,
    'postprocessors': [{
        'key': 'FFmpegExtractAudio',
        'preferredcodec': 'mp3',
        'preferredquality': '192',
    }],
}

# Modelo para validação de entrada
class SearchQuery(BaseModel):
    query: str

class VideoURL(BaseModel):
    url: str

# Função para logs personalizados
def log_message(message, emoji="📢"):
    timestamp = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
    print(f"{Fore.CYAN}[{timestamp}] {emoji} {message}{Style.RESET_ALL}")
    logger.info(f"{emoji} {message}")

# Função para extrair URL válida do YouTube
def extract_youtube_url(query):
    youtube_regex = (
        r'(https?://)?(www\.)?'
        r'(youtube\.com|youtu\.be)/'
        r'(watch\?v=|embed/|v/|.+\?v=)?([^&=%\?]{11})'
    )
    match = re.match(youtube_regex, query)
    if match:
        return f"https://www.youtube.com/watch?v={match.group(5)}"
    return query

# Função para formatar duração
def format_duration(seconds):
    if not seconds:
        return "Desconhecida"
    return str(timedelta(seconds=int(seconds)))[2:]

# Página inicial
@app.get("/", response_class=HTMLResponse)
async def home(request: Request):
    return templates.TemplateResponse("index.html", {"request": request, "api_base_url": API_BASE_URL})

# Endpoint para busca de músicas
@app.post("/search")
async def search_music(query: SearchQuery):
    def fetch_info():
        with yt_dlp.YoutubeDL(YDL_OPTS) as ydl:
            try:
                info = ydl.extract_info(f"ytsearch:{query.query}", download=False)
                if info and 'entries' in info and info['entries']:
                    entry = info['entries'][0]
                    return {
                        "title": entry.get('title'),
                        "webpage_url": entry.get('webpage_url'),
                        "url": entry.get('url'),  # URL de streaming direto
                        "duration": entry.get('duration', 0),
                        "duration_string": format_duration(entry.get('duration', 0)),
                        "thumbnail": entry.get('thumbnail'),
                        "uploader": entry.get('uploader', 'Desconhecido'),
                        "id": entry.get('id')
                    }
                return None
            except Exception as e:
                log_message(f"Erro ao buscar vídeo: {e} ❌", "⚠️")
                return None

    try:
        result = await asyncio.wait_for(asyncio.to_thread(fetch_info), timeout=15.0)
        if not result:
            raise HTTPException(status_code=404, detail="Nenhum resultado encontrado para a busca.")
        log_message(f"Busca concluída para: {query.query} ✅", "🔍")
        return result
    except asyncio.TimeoutError:
        log_message(f"Timeout na busca do YouTube para: {query.query} ⏰", "⚠️")
        raise HTTPException(status_code=504, detail="Tempo esgotado ao buscar a música.")

# Endpoint para streaming de música
@app.post("/stream")
async def stream_music(video: VideoURL):
    valid_url = extract_youtube_url(video.url)

    def fetch_stream():
        with yt_dlp.YoutubeDL(YDL_OPTS) as ydl:
            try:
                info = ydl.extract_info(valid_url, download=False)
                return info.get('url'), info.get('title', 'stream')
            except Exception as e:
                log_message(f"Erro ao obter stream: {e} ❌", "⚠️")
                return None, None

    try:
        stream_url, title = await asyncio.wait_for(asyncio.to_thread(fetch_stream), timeout=15.0)
        if not stream_url:
            raise HTTPException(status_code=400, detail="Não foi possível obter o stream da música.")
        log_message(f"Streaming iniciado: {title} 📡", "▶️")
        return {"stream_url": stream_url, "title": title}
    except asyncio.TimeoutError:
        log_message(f"Timeout ao obter stream: {valid_url} ⏰", "⚠️")
        raise HTTPException(status_code=504, detail="Tempo esgotado ao obter o stream.")

# Endpoint para download de música (opcional)
@app.post("/download")
async def download_music(video: VideoURL):
    valid_url = extract_youtube_url(video.url)
    YDL_OPTS['outtmpl'] = 'temp_%(id)s.%(ext)s'

    def fetch_info():
        with yt_dlp.YoutubeDL(YDL_OPTS) as ydl:
            try:
                info = ydl.extract_info(valid_url, download=True)
                filename = ydl.prepare_filename(info).replace('.webm', '.mp3').replace('.m4a', '.mp3')
                with open(filename, 'rb') as f:
                    data = f.read()
                os.remove(filename)  # Limpar arquivo temporário
                return data, info.get('title', 'music')
            except Exception as e:
                log_message(f"Erro ao baixar vídeo: {e} ❌", "⚠️")
                return None, None

    try:
        data, title = await asyncio.wait_for(asyncio.to_thread(fetch_info), timeout=30.0)
        if not data:
            raise HTTPException(status_code=400, detail="Não foi possível baixar a música.")
        log_message(f"Música baixada: {title} 🎵", "⬇️")
        return Response(
            content=data,
            media_type="audio/mpeg",
            headers={"Content-Disposition": f"attachment; filename={title.replace(' ', '_')}.mp3"}
        )
    except asyncio.TimeoutError:
        log_message(f"Timeout ao baixar URL: {valid_url} ⏰", "⚠️")
        raise HTTPException(status_code=504, detail="Tempo esgotado ao baixar a música.")

if __name__ == "__main__":
    import uvicorn
    log_message(f"Iniciando API na porta {PORT} 🚀", "🌐")
    uvicorn.run(app, host="0.0.0.0", port=int(PORT))