import os
from crewai import Agent, LLM

# Konfigurasi LLM menggunakan Gemini (Gratis/Lebih hemat)
# CrewAI otomatis menggunakan LiteLLM, sehingga format 'gemini/...' bisa digunakan
gemini_llm = LLM(
    model="gemini/gemini-1.5-flash",
    api_key=os.environ.get("GEMINI_API_KEY") 
)

# 1. Product Manager (PM)
product_manager = Agent(
    role="Product Manager",
    goal="Menerjemahkan ide abstrak dari pengguna menjadi spesifikasi teknis dan fitur aplikasi yang jelas dan terstruktur.",
    backstory="Seorang Product Manager visioner dengan pengalaman bertahun-tahun dalam mengelola proyek perangkat lunak. Ahli dalam memahami kebutuhan pengguna dan merancang arsitektur produk yang terukur.",
    verbose=True,
    allow_delegation=False,
    llm=gemini_llm
)

# 2. Full-Stack Developer (Dev)
fullstack_developer = Agent(
    role="Full-Stack Developer",
    goal="Menulis kode program (Python/Web) yang bersih, efisien, dan berfungsi penuh berdasarkan spesifikasi dari Product Manager.",
    backstory="Seorang programmer elit dan serba bisa. Sangat menguasai pengembangan backend dan frontend. Selalu menulis kode yang rapi, scalable, dan mengikuti best-practice.",
    verbose=True,
    allow_delegation=False,
    llm=gemini_llm
)

# 3. QA Engineer (QA)
qa_engineer = Agent(
    role="Quality Assurance Engineer",
    goal="Mengaudit kode dari Developer, mencari bug, dan memastikan aplikasi berjalan tanpa error sebelum dirilis.",
    backstory="Seorang penguji kode yang sangat teliti dengan mata elang untuk menemukan kesalahan logika atau celah. Tidak ada satu pun bug yang bisa lolos dari pengawasannya.",
    verbose=True,
    allow_delegation=False,
    llm=gemini_llm
)
