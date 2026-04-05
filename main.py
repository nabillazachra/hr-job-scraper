import os
import json
import logging
import requests
from bs4 import BeautifulSoup
from datetime import datetime

# --- KONFIGURASI LOGGING ---
logging.basicConfig(level=logging.INFO, format='%(asctime)s - %(levelname)s - %(message)s')

# --- BACA SECRETS (DARI GITHUB ACTIONS) ---
TELEGRAM_BOT_TOKEN = os.getenv("TELEGRAM_BOT_TOKEN")
TELEGRAM_CHAT_ID = os.getenv("TELEGRAM_CHAT_ID")

# --- DATABASE SEDERHANA ---
DB_FILE = "processed_jobs.json"

# --- TARGET SCRAPING ---
# Contoh menggunakan JobStreet (SEEK). Kita menyematkan parameter `salary=7500000` di dalam URL targetnya
# Filter lokasi: Indonesia (bisa diganti URL pencariannya jika ingin lebih spesifik)
BASE_URL = "https://www.jobstreet.co.id/id/job-search/human-resources-jobs/?salary=7500000"

HEADERS = {
    "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36",
    "Accept-Language": "id-ID,id;q=0.9,en-US;q=0.8,en;q=0.7",
}

# --- KRITERIA FILTER ---
KEYWORDS = [
    "senior hr generalist", "hr generalist", "human capital", 
    "hcbp", "hr specialist", "hr business partner", "human resources specialist"
]
SALARY_KEYWORDS = ["7.500.000", "8.000.000", "7.5", "7,5", "8jt", "8 jt", "7.5jt", "7.5 jt"]


def load_processed_jobs():
    """Membaca file JSON yang berisi ID lowongan yang sudah pernah dikirim agar tidak dikirim ulang."""
    if os.path.exists(DB_FILE):
        try:
            with open(DB_FILE, "r") as f:
                return json.load(f)
        except Exception as e:
            logging.error(f"Gagal membaca {DB_FILE}: {e}")
            return []
    return []

def save_processed_jobs(jobs_list):
    """Menyimpan list ID lowongan kembali ke file JSON."""
    try:
        with open(DB_FILE, "w") as f:
            json.dump(jobs_list, f, indent=4)
    except Exception as e:
        logging.error(f"Gagal menyimpan ke {DB_FILE}: {e}")

def send_telegram_message(job):
    """Mengirim data lowongan pekerjaan ke API Telegram menggunakan Format Markdown."""
    if not TELEGRAM_BOT_TOKEN or not TELEGRAM_CHAT_ID:
        logging.warning("Telegram Bot Token atau Chat ID belum di-set. Melewati pengiriman pesan.")
        return False

    text = f"🎯 *Loker HR Senior Ditemukan!* 🎯\n\n"
    text += f"💼 *Posisi:* {job['title']}\n"
    text += f"🏢 *Perusahaan:* {job['company']}\n"
    text += f"📍 *Lokasi:* {job['location']}\n"
    text += f"💰 *Gaji:* {job['salary']}\n\n"
    text += f"🔗 *Link Lamaran:*\n{job['link']}"

    url = f"https://api.telegram.org/bot{TELEGRAM_BOT_TOKEN}/sendMessage"
    payload = {
        "chat_id": TELEGRAM_CHAT_ID,
        "text": text,
        "parse_mode": "Markdown",
        "disable_web_page_preview": False
    }

    try:
        response = requests.post(url, json=payload)
        response.raise_for_status()
        logging.info(f"✅ Berhasil mengirim notifikasi Telegram untuk: {job['title']}")
        return True
    except requests.exceptions.RequestException as e:
        logging.error(f"❌ Gagal mengirim ke Telegram API: {e}")
        return False

def scrape_jobs():
    """Mengambil HTML dari situs Job Portal dan mengekstrak lowongan kerja menggunakan BeautifulSoup."""
    logging.info(f"🕷️ Memulai scraping ke target URL...")
    try:
        # Melakukan Request ke Target URL
        response = requests.get(BASE_URL, headers=HEADERS, timeout=20)
        response.raise_for_status()
    except requests.exceptions.RequestException as e:
        logging.error(f"Gagal melakukan request web: {e}")
        return []

    soup = BeautifulSoup(response.text, 'html.parser')
    
    # ⚠️ PERHATIAN ⚠️
    # Data atribut `data-automation` dan tag HTML bisa berubah sewaktu-waktu tergantung pembaruan portal web (JobStreet).
    # Jika gagal scrape, cek ulang elemen menggunakan `Inspect Element` di browser.
    job_cards = soup.find_all("article", {"data-automation": "normalJob"}) 
    
    logging.info(f"Ditemukan {len(job_cards)} lowongan di halaman ini. Mulai melakukan filter...")
    found_jobs = []
    
    for card in job_cards:
        try:
            # Mengambil Judul Lowongan dan Link
            title_tag = card.find("a", {"data-automation": "jobTitle"})
            job_title = title_tag.text.strip() if title_tag else "Tanpa Judul"
            
            # Mendapatkan Link Lamaran Lengkap
            job_link = "https://www.jobstreet.co.id" + title_tag['href'] if title_tag else ""
            
            # Membuat ID unik dari URL agar kita bisa menandai apakah Loker ini sudah pernah dikabari
            job_id = title_tag['href'].split("?")[0].split("/")[-1] if title_tag else job_title
            
            # Mengambil Nama Perusahaan
            company_tag = card.find("a", {"data-automation": "jobCompany"})
            company = company_tag.text.strip() if company_tag else "Nama Perusahaan Tidak Diketahui"
            
            # Mengambil Lokasi Penempatan
            location_tag = card.find("span", {"data-automation": "jobLocation"})
            location = location_tag.text.strip() if location_tag else "Lokasi Tidak Diketahui"
            
            # Mengambil Informasi Gaji (jika public)
            salary_tag = card.find("span", {"data-automation": "jobSalary"})
            salary = salary_tag.text.strip() if salary_tag else "Gaji Disembunyikan (Berdasarkan URL Filter)"
            
            # Gabungkan semua teks dalam card ini untuk difilter lebih dalam
            description_text = card.text.lower()
            job_title_lower = job_title.lower()

            # --- LOGIKA FILTERING ---
            # 1. Cek Keyword Posisi HR/Senior
            is_keyword_match = any(kw in job_title_lower or kw in description_text for kw in KEYWORDS)
            
            # 2. Cek tambahan di teks apakah memuat rentang gaji yang dicari.
            # (Karena kita sudah menambahkan filter url "?salary=7500000", kita juga bisa bypass teks jika diperlukan,
            # Namun kita beri extra check untuk keamanan)
            is_salary_match = any(sk in salary.lower() or sk in description_text for sk in SALARY_KEYWORDS)
            
            # Tambahkan ke daftar list hanya jika mengandung keyword posisi yang ditentukan.
            # (Jika ingin mewajibkan kata kunci angka muncul eksplisit di teks juga, gunakan `and is_salary_match`)
            if is_keyword_match:
                found_jobs.append({
                    "id": job_id,
                    "title": job_title,
                    "company": company,
                    "location": location,
                    "salary": salary, # Bisa eksplisit ditambahkan hasil jika cocok
                    "link": job_link
                })
        except Exception as e:
            logging.error(f"Terjadi error saat parse satu card pekerjaan: {e}")
            continue
            
    return found_jobs

def main():
    processed_jobs = load_processed_jobs()
    scraped_jobs = scrape_jobs()
    
    new_jobs_count = 0
    
    for job in scraped_jobs:
        # Cek apakah ID dari lowongan ini belum pernah diproses
        if job["id"] not in processed_jobs:
            success = send_telegram_message(job)
            if success:
                # Simpan ke daftar agar pacar kamu tidak di-spam dengan job yang sama lagi
                processed_jobs.append(job["id"])
                new_jobs_count += 1
    
    # Hanya simpan ulang ke database file JSON kalau memang ada pekerjaan baru
    if new_jobs_count > 0:
        save_processed_jobs(processed_jobs)
        logging.info(f"Pengecekan selesai! Mengirim {new_jobs_count} loker baru ke Telegram.")
    else:
        logging.info("Pengecekan selesai. Tidak ada loker baru yang belum pernah dikirim.")

if __name__ == "__main__":
    main()
