import os
import sys
import json
import asyncio
import threading
import websockets
import time
import requests
from dotenv import load_dotenv
from crewai import Crew, Process

# Pastikan encoding terminal mendukung karakter khusus dari AI
if sys.stdout.encoding.lower() != 'utf-8':
    try:
        sys.stdout.reconfigure(encoding='utf-8')
    except Exception:
        pass

load_dotenv()

from agents import product_manager, fullstack_developer, qa_engineer
from tasks import task_planning, task_coding, task_testing

# Setup Asyncio Event Loop
loop = asyncio.new_event_loop()
# Inisialisasi antrian (Queue) untuk komunikasi antar-thread (CrewAI ke WebSocket)
event_queue = asyncio.Queue()

# Menyimpan semua koneksi WebSocket yang aktif
CONNECTED_CLIENTS = set()

# Konfigurasi LLM menggunakan library pydantic & langchain
os.environ["GEMINI_API_KEY"] = os.getenv("GEMINI_API_KEY")

# Fungsi Callback: Dipanggil setiap kali Agen melakukan sebuah langkah (step)
def agent_callback(agent_name, step_output):
    try:
        # Konversi output AI menjadi string, batasi panjangnya jika terlalu besar
        action = str(step_output)
        if len(action) > 300:
            action = action[:297] + "..."
            
        # Petakan nama agen ke ID frontend dan status kerja yang sesuai
        agent_id = "product_manager"
        status_val = "coding"
        
        name_lower = agent_name.lower()
        if "product manager" in name_lower or "pm" in name_lower:
            agent_id = "product_manager"
            status_val = "coding"
        elif "full-stack developer" in name_lower or "developer" in name_lower:
            agent_id = "fullstack_developer"
            status_val = "coding"
        elif "quality assurance" in name_lower or "qa" in name_lower:
            agent_id = "qa_engineer"
            status_val = "testing"
            
        # Format payload data menjadi JSON sesuai ekspektasi parser OfficeScreen
        message = json.dumps({
            "agent": agent_id,
            "status": status_val,
            "action": action
        })
        
        # Masukkan pesan ke dalam antrean (Thread-safe)
        loop.call_soon_threadsafe(event_queue.put_nowait, message)
        
        # Tambahkan jeda waktu 10 detik agar tidak terkena 429 Rate Limit Gemini
        print(f"⏳ [Rate Limiter] Memberikan jeda 10 detik untuk {agent_name}...", flush=True)
        time.sleep(10)
    except Exception as e:
        print(f"[Warning] Error pada callback: {e}")

# Pasang callback secara dinamis ke semua agen kita
product_manager.step_callback = lambda step: agent_callback("Product Manager", step)
fullstack_developer.step_callback = lambda step: agent_callback("Full-Stack Developer", step)
qa_engineer.step_callback = lambda step: agent_callback("QA Engineer", step)

# Patch method execute_task agar memancarkan status mulai dan selesai bekerja secara terpercaya
def patch_agent_execute_task(agent, agent_name):
    import types
    original_execute_task = agent.execute_task
    
    def wrapped_execute_task(self, *args, **kwargs):
        agent_id = "product_manager"
        status_val = "coding"
        
        name_lower = agent_name.lower()
        if "product manager" in name_lower or "pm" in name_lower:
            agent_id = "product_manager"
            status_val = "coding"
        elif "full-stack developer" in name_lower or "developer" in name_lower:
            agent_id = "fullstack_developer"
            status_val = "coding"
        elif "quality assurance" in name_lower or "qa" in name_lower:
            agent_id = "qa_engineer"
            status_val = "testing"
            
        print(f"📡 [Broadcast] {agent_name} mulai bekerja...", flush=True)
        message_working = json.dumps({
            "agent": agent_id,
            "status": status_val,
            "action": f"Memulai tugas: {agent_name}"
        })
        loop.call_soon_threadsafe(event_queue.put_nowait, message_working)
        
        # Eksekusi task yang asli
        result = original_execute_task(*args, **kwargs)
        
        print(f"📡 [Broadcast] {agent_name} selesai bekerja, kembali idle.", flush=True)
        message_idle = json.dumps({
            "agent": agent_id,
            "status": "idle",
            "action": f"Menyelesaikan tugas: {agent_name}"
        })
        loop.call_soon_threadsafe(event_queue.put_nowait, message_idle)
        
        # Jeda tambahan antar-tugas agar user sempat melihat perubahan status di visualisasi
        print(f"⏳ [Rate Limiter] Memberikan jeda 10 detik setelah {agent_name} selesai...", flush=True)
        time.sleep(10)
        
        return result
        
    bound_method = types.MethodType(wrapped_execute_task, agent)
    object.__setattr__(agent, 'execute_task', bound_method)

# Terapkan patch ke semua agen
patch_agent_execute_task(product_manager, "Product Manager")
patch_agent_execute_task(fullstack_developer, "Full-Stack Developer")
patch_agent_execute_task(qa_engineer, "QA Engineer")

# Handler Koneksi WebSocket (Mendengarkan Client Frontend)
async def websocket_handler(websocket):
    CONNECTED_CLIENTS.add(websocket)
    print(f"🔗 Klien 3D Frontend terhubung ke WebSocket! Total klien: {len(CONNECTED_CLIENTS)}", flush=True)
    try:
        # 1. Kirim mock connect.challenge agar Claw3D mulai mengirim auth
        await websocket.send(json.dumps({
            "type": "event",
            "event": "connect.challenge",
            "payload": {"nonce": "mock-nonce-123"}
        }))
        
        # 2. Tunggu respon connect dari frontend
        try:
            req_str = await asyncio.wait_for(websocket.recv(), timeout=5.0)
            print(f"RECEIVED FROM BROWSER: {req_str}", flush=True)
            req = json.loads(req_str)
            
            # 3. Balas dengan success OK (GatewayHelloOk)
            if req.get("type") == "req" and req.get("method") == "connect":
                req_id = req.get("id", "1")
                await websocket.send(json.dumps({
                    "type": "res",
                    "id": req_id,
                    "ok": True,
                    "payload": {
                        "type": "hello-ok",
                        "protocol": 4,
                        "adapterType": "custom"
                    }
                }))
                print("✅ Handshake Connect sukses dikirim!", flush=True)
        except Exception as e:
            print(f"ERROR saat handshake: {e}", flush=True)

        # 4. Jaga koneksi tetap hidup dengan menunggu pesan (atau penutupan koneksi)
        async for message in websocket:
            pass # Abaikan pesan dari klien (kita hanya mengirim event ke mereka)
            
    except websockets.exceptions.ConnectionClosed:
        pass
    finally:
        CONNECTED_CLIENTS.remove(websocket)
        print(f"🔗 Klien 3D Frontend terputus. Total klien: {len(CONNECTED_CLIENTS)}", flush=True)

# Broadcaster di background (membaca dari event_queue dan mengirim ke SEMUA klien aktif)
async def broadcast_events():
    while True:
        message = await event_queue.get()
        if CONNECTED_CLIENTS:
            out_msg = json.dumps({
                "type": "event",
                "event": "agent.status",
                "payload": json.loads(message)
            })
            websockets.broadcast(CONNECTED_CLIENTS, out_msg)

# Fungsi untuk membypass pengecekan keamanan asal silang (CORS) dari browser
async def process_request(path, request_headers):
    return None

# Memulai Server WebSocket
async def start_server():
    print("🌐 Menjalankan WebSocket Server di ws://127.0.0.1:8000...")
    # Jalankan broadcaster di background
    asyncio.create_task(broadcast_events())
    
    async with websockets.serve(websocket_handler, "0.0.0.0", 8000, process_request=process_request):
        await asyncio.Future()  # Berjalan selamanya (infinite loop)

# Fungsi untuk mengirim notifikasi ke Discord
def kirim_ke_discord(pesan):
    webhook_url = os.environ.get("DISCORD_WEBHOOK_URL")
    if not webhook_url:
        print("⚠️ DISCORD_WEBHOOK_URL belum disetel di .env. Laporan tidak dikirim ke Discord.")
        return
        
    try:
        data = {
            "content": pesan,
            "username": "AI Office Bot",
            "avatar_url": "https://cdn-icons-png.flaticon.com/512/4712/4712010.png"
        }
        response = requests.post(webhook_url, json=data)
        if response.status_code in [200, 204]:
            print("✅ Laporan berhasil dikirim ke Discord!")
        else:
            print(f"❌ Gagal mengirim ke Discord: {response.status_code} - {response.text}")
    except Exception as e:
        print(f"❌ Error saat mengirim ke Discord: {e}")

# Fungsi untuk mengeksekusi CrewAI (Berjalan di Thread terpisah)
def run_crewai():
    print("🚀 Memulai eksekusi CrewAI di latar belakang...")
    developer_crew = Crew(
        agents=[product_manager, fullstack_developer, qa_engineer],
        tasks=[task_planning, task_coding, task_testing],
        process=Process.sequential,
        verbose=True
    )
    
    # Memulai kerja agen
    result = developer_crew.kickoff()
    
    # Jika sudah selesai, simpan hasilnya
    try:
        with open("hasil_kerja_v2.md", "w", encoding="utf-8") as file:
            file.write(str(result))
    except Exception:
        pass
        
    print("\n[SUKSES] Eksekusi selesai. Laporan disimpan ke 'hasil_kerja_v2.md'")
    
    # 4. Kirim notifikasi hasil ke Discord
    pesan_laporan = (
        "🚀 **Laporan AI Office Selesai**\n"
        "Tim AI Developer telah menyelesaikan tugas terbaru!\n\n"
        f"**Hasil / Output Ringkas:**\n```text\n{str(result)[:1500]}...\n```\n"
        "*(Buka file hasil_kerja_v2.md untuk laporan lengkap)*"
    )
    kirim_ke_discord(pesan_laporan)
    
    # Kirim status selesai ke frontend
    loop.call_soon_threadsafe(
        event_queue.put_nowait, 
        json.dumps({"agent": "System", "status": "completed", "action": "Semua tugas selesai!"})
    )

def main():
    # 1. Jalankan proses berat AI (CrewAI) di thread terpisah agar tidak memblokir server
    crew_thread = threading.Thread(target=run_crewai)
    crew_thread.daemon = True
    crew_thread.start()

    # 2. Jalankan server jaringan (WebSocket) di thread utama menggunakan event loop
    asyncio.set_event_loop(loop)
    try:
        loop.run_until_complete(start_server())
    except KeyboardInterrupt:
        print("\nServer dimatikan oleh pengguna.")

if __name__ == "__main__":
    main()
