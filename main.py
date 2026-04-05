import os
import json
import logging
import requests
import time
from bs4 import BeautifulSoup
from datetime import datetime

# --- KONFIGURASI LOGGING ---
logging.basicConfig(level=logging.INFO, format='%(asctime)s - %(levelname)s - %(message)s')

# --- BACA SECRETS (DARI GITHUB ACTIONS) ---
TELEGRAM_BOT_TOKEN = os.getenv("TELEGRAM_BOT_TOKEN")
TELEGRAM_CHAT_ID = os.getenv("TELEGRAM_CHAT_ID")

DB_FILE = "processed_jobs.json"

# --- TARGET SCRAPING (LINKEDIN GUEST API) ---
# Menggunakan RSS / Guest API milik LinkedIn yang terbukti TIDAK diblokir oleh sistem Cloudflare GitHub Actions.
# URL mencari lowongan dengan kata kunci "hr" di seluruh Indonesia
BASE_URL = "https://www.linkedin.com/jobs-guest/jobs/api/seeMoreJobPostings/search?keywords=senior%20human%20resources&location=Indonesia&start=0"

HEADERS = {
    "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36",
    "Accept-Language": "id-ID,id;q=0.9,en-US;q=0.8,en;q=0.7",
}

KEYWORDS = [
    "senior hr generalist", "hr generalist", "human capital", 
    "hcbp", "hr specialist", "hr business partner", "human resources specialist", "hrbp",
    "human resource", "hr operation", "hr manager", "hr"
]
SALARY_KEYWORDS = ["7.500.000", "8.000.000", "7.5", "7,5", "8jt", "8 jt", "7.5jt", "7.5 jt"]


def load_processed_jobs():
    if os.path.exists(DB_FILE):
        try:
            with open(DB_FILE, "r") as f:
                return json.load(f)
        except Exception as e:
            logging.error(f"Gagal membaca {DB_FILE}: {e}")
            return []
    return []

def save_processed_jobs(jobs_list):
    try:
        with open(DB_FILE, "w") as f:
            json.dump(jobs_list, f, indent=4)
    except Exception as e:
        logging.error(f"Gagal menyimpan ke {DB_FILE}: {e}")

def get_job_description(url):
    """Mengambil rincian full loker untuk mencari angka gaji"""
    try:
        # Tambahkan delay agar tidak di block karena ngespam terlalu cepat
        time.sleep(1) 
        response = requests.get(url, headers=HEADERS, timeout=10)
        soup = BeautifulSoup(response.text, 'html.parser')
        desc_div = soup.find('div', class_='show-more-less-html__markup')
        if desc_div:
            return desc_div.text.strip().lower()
    except Exception:
        return ""
    return ""

def send_telegram_message(job):
    if not TELEGRAM_BOT_TOKEN or not TELEGRAM_CHAT_ID:
        logging.warning("Telegram Bot Token atau Chat ID belum di-set. Melewati pengiriman pesan.")
        return False

    text = f"🎯 *Loker HR Senior Ditemukan!* 🎯\n\n"
    text += f"💼 *Posisi:* {job['title']}\n"
    text += f"🏢 *Perusahaan:* {job['company']}\n"
    text += f"📍 *Lokasi:* {job['location']}\n"
    text += f"✨ *Platform:* LinkedIn\n\n"
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
    logging.info(f"🕷️ Memulai scraping ke LinkedIn Jobs Target URL...")
    try:
        response = requests.get(BASE_URL, headers=HEADERS, timeout=20)
        response.raise_for_status()
    except requests.exceptions.RequestException as e:
        logging.error(f"Gagal melakukan request web: {e}")
        return []

    soup = BeautifulSoup(response.text, 'html.parser')
    job_cards = soup.find_all("div", class_="base-search-card")
    
    logging.info(f"Ditemukan {len(job_cards)} lowongan HR di halaman ini. Mulai melakukan filter...")
    found_jobs = []
    
    # Batasi pengambilan untuk performa karena GitHub Actions dibatasi limit menit gratis dan LinkedIn gampang memblokir request beruntun
    for card in job_cards[:15]: 
        try:
            title_tag = card.find("h3", class_="base-search-card__title")
            job_title = title_tag.text.strip() if title_tag else "Tanpa Judul"
            
            link_tag = card.find("a", class_="base-card__full-link")
            job_link = link_tag['href'] if link_tag else ""
            
            # Memisahkan ID unik linkedin
            # Contoh https://id.linkedin.com/jobs/view/human-resources...-4390431163?position=...
            job_id = job_link.split('?')[0].split('-')[-1] if job_link else job_title
            
            company_tag = card.find("h4", class_="base-search-card__subtitle")
            company = company_tag.text.strip() if company_tag else "Perusahaan Tidak Diketahui"
            
            location_tag = card.find("span", class_="job-search-card__location")
            location = location_tag.text.strip() if location_tag else "Lokasi Tidak Diketahui"
            
            job_title_lower = job_title.lower()

            # --- LOGIKA FILTERING ---
            is_keyword_match = any(kw in job_title_lower for kw in KEYWORDS)
            
            if is_keyword_match:
                # Opsi Tambahan: Mengambil deskripsi spesifik job untuk mencari angka gaji
                # desc = get_job_description(job_link)
                # print(f"Deskripsi loker length: {len(desc)}")
                
                # Kita tidak filter paksa keyword gaji di sini karena banyak lowongan tidak mencantumkannya secara eksplisit.
                found_jobs.append({
                    "id": job_id,
                    "title": job_title,
                    "company": company,
                    "location": location,
                    "link": job_link
                })
                
        except Exception as e:
            logging.error(f"Terjadi error saat parse satu job: {e}")
            continue
            
    return found_jobs

def main():
    processed_jobs = load_processed_jobs()
    scraped_jobs = scrape_jobs()
    
    new_jobs_count = 0
    
    for job in scraped_jobs:
        if job["id"] not in processed_jobs:
            success = send_telegram_message(job)
            if success:
                processed_jobs.append(job["id"])
                new_jobs_count += 1
                time.sleep(2) # Delay telegram API spam protection
    
    if new_jobs_count > 0:
        save_processed_jobs(processed_jobs)
        logging.info(f"Pengecekan selesai! Mengirim {new_jobs_count} loker baru ke Telegram.")
    else:
        logging.info("Pengecekan selesai. Tidak ada loker spesifik baru yang belum pernah dikirim.")

if __name__ == "__main__":
    main()
