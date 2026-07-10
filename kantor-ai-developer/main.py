import os
import sys
import json
import asyncio
import types
import uuid
import websockets
import time
import requests
from dotenv import load_dotenv
from crewai import Crew, Process, Task
import discord
from discord import app_commands

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

# Counter untuk sequence number event gateway
_event_seq = 0
def next_seq():
    global _event_seq
    _event_seq += 1
    return _event_seq

# Mapping agent_id -> session key (sesuai format Claw3D gateway)
AGENT_SESSION_KEYS = {
    "product_manager": "agent:product_manager:main",
    "fullstack_developer": "agent:fullstack_developer:main",
    "qa_engineer": "agent:qa_engineer:main",
}

# Tracking run IDs per agent
_active_run_ids = {}

# Validasi konfigurasi .env saat startup
_gemini_key = os.getenv("GEMINI_API_KEY")
if not _gemini_key:
    print("❌ FATAL: GEMINI_API_KEY tidak ditemukan di .env! Agen tidak akan bisa berpikir.")
    sys.exit(1)

# Setup Discord Bot
discord_intents = discord.Intents.default()
discord_client = discord.Client(intents=discord_intents)
tree = app_commands.CommandTree(discord_client)
is_crew_running = False

# Fungsi Callback: Dipanggil setiap kali Agen melakukan sebuah langkah (step)
# (Hanya untuk logging terminal, visual update ditangani oleh lifecycle events)
def agent_callback(agent_name, step_output):
    try:
        action = str(step_output)
        if len(action) > 300:
            action = action[:297] + "..."
        print(f"🔄 [Step] {agent_name}: {action[:100]}...", flush=True)
    except Exception as e:
        print(f"[Warning] Error pada callback: {e}")

# Pasang callback secara dinamis ke semua agen kita
product_manager.step_callback = lambda step: agent_callback("Product Manager", step)
fullstack_developer.step_callback = lambda step: agent_callback("Full-Stack Developer", step)
qa_engineer.step_callback = lambda step: agent_callback("QA Engineer", step)

# Helper: Membuat event frame gateway yang valid untuk Claw3D
def build_agent_lifecycle_event(agent_id, phase, run_id):
    """Membuat event frame sesuai protokol gateway Claw3D.
    
    Frontend mengklasifikasikan event berdasarkan nama:
    - 'agent' -> runtime-agent (lifecycle start/end/error)
    - 'chat'  -> runtime-chat  (streaming pesan)
    - 'presence'/'heartbeat' -> summary-refresh
    - lainnya -> diabaikan (ignore)
    
    Payload untuk 'agent' lifecycle harus memiliki:
    - runId: string unik per eksekusi
    - sessionKey: format 'agent:<id>:main'
    - stream: 'lifecycle'
    - data: { phase: 'start' | 'end' | 'error' }
    """
    session_key = AGENT_SESSION_KEYS.get(agent_id, f"agent:{agent_id}:main")
    return json.dumps({
        "type": "event",
        "event": "agent",
        "seq": next_seq(),
        "payload": {
            "runId": run_id,
            "sessionKey": session_key,
            "stream": "lifecycle",
            "data": {
                "phase": phase
            }
        }
    })

# Patch method execute_task agar memancarkan lifecycle event yang dipahami Claw3D
def patch_agent_execute_task(agent, agent_name):
    original_execute_task = agent.execute_task
    
    def wrapped_execute_task(self, *args, **kwargs):
        agent_id = "product_manager"
        
        name_lower = agent_name.lower()
        if "product manager" in name_lower or "pm" in name_lower:
            agent_id = "product_manager"
        elif "full-stack developer" in name_lower or "developer" in name_lower:
            agent_id = "fullstack_developer"
        elif "quality assurance" in name_lower or "qa" in name_lower:
            agent_id = "qa_engineer"
        
        # Buat run ID unik untuk sesi kerja agen ini
        run_id = f"run-{agent_id}-{uuid.uuid4().hex[:8]}"
        _active_run_ids[agent_id] = run_id
            
        # Kirim lifecycle START event
        print(f"📡 [Lifecycle] {agent_name} mulai bekerja (runId: {run_id})...", flush=True)
        start_event = build_agent_lifecycle_event(agent_id, "start", run_id)
        loop.call_soon_threadsafe(event_queue.put_nowait, start_event)
        
        try:
            # Eksekusi task yang asli
            result = original_execute_task(*args, **kwargs)
            
            # Kirim lifecycle END event
            print(f"📡 [Lifecycle] {agent_name} selesai bekerja (runId: {run_id}).", flush=True)
            end_event = build_agent_lifecycle_event(agent_id, "end", run_id)
            loop.call_soon_threadsafe(event_queue.put_nowait, end_event)
        except Exception as e:
            # Kirim lifecycle ERROR event
            print(f"📡 [Lifecycle] {agent_name} error (runId: {run_id}): {e}", flush=True)
            error_event = build_agent_lifecycle_event(agent_id, "error", run_id)
            loop.call_soon_threadsafe(event_queue.put_nowait, error_event)
            raise
        finally:
            _active_run_ids.pop(agent_id, None)
        
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
            # Event sudah dalam format gateway frame lengkap (type+event+payload)
            # dari build_agent_lifecycle_event(), jadi kirim langsung
            websockets.broadcast(CONNECTED_CLIENTS, message)

# Fungsi untuk membypass pengecekan keamanan asal silang (CORS) dari browser
async def process_request(path, request_headers):
    return None

# Memulai Server WebSocket
async def start_server():
    port = int(os.environ.get("PORT", 8000))
    print(f"🌐 Menjalankan WebSocket Server di ws://0.0.0.0:{port}...")
    # Jalankan broadcaster di background
    asyncio.create_task(broadcast_events())
    
    async with websockets.serve(websocket_handler, "0.0.0.0", port, process_request=process_request):
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
def run_crewai(tugas_custom=None):
    global is_crew_running
    print("🚀 Memulai eksekusi CrewAI di latar belakang...")
    
    # Buat Task BARU setiap kali dieksekusi agar tidak bermutasi objek global
    if tugas_custom:
        desc_planning = (
            f"Buat dokumen spesifikasi detail untuk aplikasi: '{tugas_custom}'. "
            "Sertakan panduan alur program (user flow) dan struktur data yang direkomendasikan."
        )
        desc_coding = (
            f"Tulis kode program Python yang utuh dan siap dijalankan untuk aplikasi: '{tugas_custom}'. "
            "Gunakan dokumen spesifikasi dari Product Manager sebagai acuan utama. "
            "Pastikan kode ditulis dengan rapi (Clean Code), efisien, dan memiliki komentar yang memudahkan pemahaman."
        )
        desc_testing = (
            f"Audit dan periksa kode Python yang ditulis oleh Full-Stack Developer untuk aplikasi: '{tugas_custom}'. "
            "Lakukan simulasi pengujian (mental walk-through) untuk setiap fitur dan cari kemungkinan bug/error. "
            "Jika ada bug, buat laporan perbaikan dan tulis ulang kode yang sudah diperbaiki. Jika sudah sempurna, berikan persetujuan akhir."
        )
    else:
        desc_planning = task_planning.description
        desc_coding = task_coding.description
        desc_testing = task_testing.description
    
    run_task_planning = Task(
        description=desc_planning,
        expected_output="Dokumen spesifikasi teknis dan fitur yang terstruktur jelas dalam format Markdown.",
        agent=product_manager
    )
    run_task_coding = Task(
        description=desc_coding,
        expected_output="Satu blok kode Python utuh yang berfungsi penuh (tanpa kode yang terpotong atau sekadar placeholder).",
        agent=fullstack_developer
    )
    run_task_testing = Task(
        description=desc_testing,
        expected_output="Laporan pengujian (QA Report) beserta kode Python versi final yang dijamin bebas error.",
        agent=qa_engineer
    )
    
    developer_crew = Crew(
        agents=[product_manager, fullstack_developer, qa_engineer],
        tasks=[run_task_planning, run_task_coding, run_task_testing],
        process=Process.sequential,
        verbose=True
    )
    
    # Memulai kerja agen
    try:
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
        
        print("✅ Semua tugas selesai! Lifecycle END sudah terkirim per-agen.", flush=True)
    except Exception as e:
        error_msg = f"❌ **CrewAI Error:**\nTerjadi kesalahan saat mengeksekusi agen:\n```text\n{str(e)}\n```"
        print(error_msg)
        kirim_ke_discord(error_msg)
    finally:
        is_crew_running = False

@discord_client.event
async def on_ready():
    await tree.sync()
    print(f"🤖 Discord Bot Controller siap! Login sebagai {discord_client.user}", flush=True)
    
@tree.command(name="status", description="Cek status server WebSocket dan CrewAI")
async def cmd_status(interaction: discord.Interaction):
    status_crew = "🟢 Sedang Bekerja (Running)" if is_crew_running else "⚪ Menganggur (Idle)"
    pesan = (
        f"**Server Status:**\n"
        f"🔌 WebSocket Clients: {len(CONNECTED_CLIENTS)}\n"
        f"🤖 CrewAI Status: {status_crew}"
    )
    await interaction.response.send_message(pesan)
    
@tree.command(name="kerjakan", description="Perintahkan agen untuk mengerjakan proyek/tugas baru")
@app_commands.describe(tugas="Deskripsi aplikasi atau tugas yang harus dikerjakan")
async def cmd_kerjakan(interaction: discord.Interaction, tugas: str):
    global is_crew_running
    if is_crew_running:
        await interaction.response.send_message("⚠️ Agen masih mengerjakan tugas lain! Tunggu sampai selesai ya bos.")
        return
        
    is_crew_running = True
    await interaction.response.send_message(f"🚀 **Perintah Diterima!**\nAgen segera mengerjakan: *{tugas}*")
    
    # Eksekusi CrewAI di background tanpa memblokir event loop
    asyncio.create_task(asyncio.to_thread(run_crewai, tugas))

def main():
    asyncio.set_event_loop(loop)
    
    token = os.environ.get("DISCORD_BOT_TOKEN")
    if not token:
        print("⚠️ DISCORD_BOT_TOKEN tidak ditemukan di .env. Hanya menjalankan WebSocket Server.")
        try:
            loop.run_until_complete(start_server())
        except KeyboardInterrupt:
            print("\nServer dimatikan oleh pengguna.")
        return
        
    async def main_async():
        ws_task = asyncio.create_task(start_server())
        bot_task = asyncio.create_task(discord_client.start(token))
        await asyncio.gather(ws_task, bot_task)

    try:
        loop.run_until_complete(main_async())
    except KeyboardInterrupt:
        print("\nServer dimatikan oleh pengguna.")
    except Exception as e:
        print(f"\n❌ ERROR FATAL SAAT STARTUP: {e}")
        import traceback
        traceback.print_exc()

if __name__ == "__main__":
    main()
