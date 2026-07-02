from crewai import Task
from agents import product_manager, fullstack_developer, qa_engineer

# 1. Tugas Perencanaan (Dikerjakan oleh PM)
task_planning = Task(
    description=(
        "Buat dokumen spesifikasi detail untuk aplikasi 'Penghitung Pengeluaran Harian (Budget Tracker)' berbasis CLI menggunakan Python. "
        "Fitur minimal yang harus ada: 1) Tambah pengeluaran, 2) Lihat total pengeluaran, 3) Lihat riwayat pengeluaran. "
        "Sertakan panduan alur program (user flow) dan struktur data yang direkomendasikan."
    ),
    expected_output="Dokumen spesifikasi teknis dan fitur yang terstruktur jelas dalam format Markdown.",
    agent=product_manager
)

# 2. Tugas Coding (Dikerjakan oleh Developer)
task_coding = Task(
    description=(
        "Tulis kode program Python yang utuh dan siap dijalankan untuk aplikasi Budget Tracker berbasis CLI. "
        "Gunakan dokumen spesifikasi dari Product Manager sebagai acuan utama. "
        "Pastikan kode ditulis dengan rapi (Clean Code), efisien, dan memiliki komentar yang memudahkan pemahaman."
    ),
    expected_output="Satu blok kode Python utuh yang berfungsi penuh (tanpa kode yang terpotong atau sekadar placeholder).",
    agent=fullstack_developer
)

# 3. Tugas Pengujian/QA (Dikerjakan oleh QA)
task_testing = Task(
    description=(
        "Audit dan periksa kode Python yang ditulis oleh Full-Stack Developer. "
        "Lakukan simulasi pengujian (mental walk-through) untuk setiap fitur (tambah, lihat total, lihat riwayat) dan cari kemungkinan bug/error. "
        "Jika ada bug, buat laporan perbaikan dan tulis ulang kode yang sudah diperbaiki. Jika sudah sempurna, berikan persetujuan akhir."
    ),
    expected_output="Laporan pengujian (QA Report) beserta kode Python versi final yang dijamin bebas error.",
    agent=qa_engineer
)
