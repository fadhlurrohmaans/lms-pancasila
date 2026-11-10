import os
import json
import io
import re
import random
import string
import hashlib
import time
import base64
import urllib.parse
from datetime import datetime

import streamlit as st
import streamlit.components.v1 as components
import firebase_admin
from firebase_admin import credentials, firestore
import pandas as pd
import google.generativeai as genai

# Impor FirestoreBundle untuk penanganan Firestore Data Bundles
try:
    from google.cloud.firestore_bundle import FirestoreBundle
except ImportError:
    try:
        from google.cloud.firestore_v1.bundle import FirestoreBundle
    except ImportError:
        FirestoreBundle = None

# ==========================================
# 1. CONFIG & STYLING
# ==========================================
st.set_page_config(
    page_title="LMS Pendidikan Pancasila",
    page_icon="🇮🇩",
    layout="wide",
    initial_sidebar_state="collapsed"
)

st.markdown("""
    <style>
    html, body, [class*="css"] {
        font-family: -apple-system, BlinkMacSystemFont, "Segoe UI", Roboto, Helvetica, Arial, sans-serif;
    }
    input[type="text"], input[type="password"], textarea, select { 
        font-size: 16px !important;
        border-radius: 10px !important;
    }
    @media (max-width: 768px) {
        .main .block-container { padding: 0.8rem 0.6rem 3rem !important; }
        [data-testid="column"] { width: 100% !important; flex: 1 1 100% !important; min-width: 100% !important; margin-bottom: 0.5rem; }
        .stButton > button, .stDownloadButton > button { 
            width: 100% !important; min-height: 50px !important; font-size: 16px !important; font-weight: bold; border-radius: 12px !important; 
        }
        h1 { font-size: 1.6rem !important; } 
        h2 { font-size: 1.3rem !important; } 
        h3 { font-size: 1.1rem !important; }
    }
    .student-header {
        background: linear-gradient(135deg, #1e3c72 0%, #2a5298 100%);
        color: white; padding: 18px 20px; border-radius: 16px; margin-bottom: 15px;
    }
    .stTabs [data-baseweb="tab-list"] { gap: 6px; overflow-x: auto; white-space: nowrap; border-bottom: 2px solid #eaeaea; padding-bottom: 4px; }
    .stTabs [data-baseweb="tab"] { padding: 10px 18px; border-radius: 20px; font-weight: 600; }
    .stTabs [aria-selected="true"] { background-color: #1e3c72 !important; color: white !important; }
    
    .cbt-container {
        background-color: #ffffff;
        padding: 24px;
        border-radius: 16px;
        box-shadow: 0 4px 15px rgba(0,0,0,0.05);
        border: 1px solid #e1e8ed;
        margin-bottom: 20px;
    }
    .soal-box {
        background-color: #f8fafc;
        border: 1px solid #e2e8f0;
        border-radius: 12px;
        padding: 18px;
        margin-bottom: 20px;
    }
    </style>
""", unsafe_allow_html=True)

# ==========================================
# 2. FIREBASE & CACHING OPTIMIZATION
# ==========================================
@st.cache_resource
def init_firebase():
    if not firebase_admin._apps:
        key_dict = dict(st.secrets["firebase"])
        if "\\n" in key_dict["private_key"]:
            key_dict["private_key"] = key_dict["private_key"].replace("\\n", "\n")
        cred = credentials.Certificate(key_dict)
        firebase_admin.initialize_app(cred)
    return firestore.client()

try:
    db = init_firebase()
except Exception as e:
    st.error(f"Gagal terhubung ke Firebase: {e}")
    st.stop()

# --- FIRESTORE AGGREGATION QUERY HELPERS ---
@st.cache_data(ttl=60)
def count_siswa_by_kelas(kelas):
    try:
        query = db.collection("users").where("role", "==", "siswa").where("kelas", "==", kelas)
        res = query.count().get()
        return res[0][0].value
    except Exception:
        return 0

@st.cache_data(ttl=60)
def count_all_users(role_filter=None):
    try:
        query = db.collection("users")
        if role_filter and role_filter != "semua":
            query = query.where("role", "==", role_filter)
        res = query.count().get()
        return res[0][0].value
    except Exception:
        return 0

@st.cache_data(ttl=60)
def count_submitted_by_tugas_kelas(tugas_id, kelas):
    try:
        query = db.collection("pengerjaan_siswa").where("id_tugas", "==", tugas_id).where("kelas_siswa", "==", kelas).where("status", "==", "submitted")
        res = query.count().get()
        return res[0][0].value
    except Exception:
        return 0

@st.cache_data(ttl=60)
def get_guru_dashboard_stats(pilihan_kelas_tuple):
    try:
        total_materi = db.collection("materi_pancasila").count().get()[0][0].value
    except Exception:
        total_materi = 0

    try:
        total_tugas = db.collection("tugas_pancasila").count().get()[0][0].value
    except Exception:
        total_tugas = 0

    total_siswa = 0
    for k in pilihan_kelas_tuple:
        total_siswa += count_siswa_by_kelas(k)

    total_submitted = 0
    for k in pilihan_kelas_tuple:
        try:
            q_sub = db.collection("pengerjaan_siswa").where("kelas_siswa", "==", k).where("status", "==", "submitted")
            total_submitted += q_sub.count().get()[0][0].value
        except Exception:
            pass

    return {
        "total_siswa": total_siswa,
        "total_tugas": total_tugas,
        "total_materi": total_materi,
        "total_submitted": total_submitted
    }

# --- OPTIMIZED CACHED READ FUNCTIONS ---
@st.cache_data(ttl=86400)
def ensure_default_admin_created():
    admin_ref = db.collection("users").document("admin")
    if not admin_ref.get().exists:
        admin_ref.set({
            "nama": "Super Admin",
            "role": "superadmin",
            "password": hash_pass("admin123"),
            "password_plain": "admin123",
            "created_at": firestore.SERVER_TIMESTAMP
        })
        return True
    return False

@st.cache_data(ttl=86400)
def get_all_kelas():
    doc = db.collection("config").document("master_kelas").get()
    if doc.exists:
        return sorted(doc.to_dict().get("daftar", []))
    return []

@st.cache_data(ttl=300)
def get_users_paginated(limit=10, offset=0, role_filter=None):
    query = db.collection("users")
    if role_filter and role_filter != "semua":
        query = query.where("role", "==", role_filter)
    query = query.limit(limit).offset(offset)
    docs = query.stream()
    return [{"id": d.id, **d.to_dict()} for d in docs]

@st.cache_data(ttl=600)
def get_all_users_cached(limit=500):
    docs = db.collection("users").limit(limit).stream()
    return [{"id": d.id, **d.to_dict()} for d in docs]

@st.cache_data(ttl=300)
def get_siswa_by_kelas_cached(kelas, limit=150, offset=0):
    docs = db.collection("users").where("role", "==", "siswa").where("kelas", "==", kelas).limit(limit).offset(offset).stream()
    return [{"username": d.id, **d.to_dict()} for d in docs]

@st.cache_data(ttl=300)
def get_all_tugas_cached(limit=100, offset=0):
    docs = db.collection("tugas_pancasila").limit(limit).offset(offset).stream()
    return [{"id": d.id, **d.to_dict()} for d in docs]

@st.cache_data(ttl=300)
def get_all_materi_cached(limit=100, offset=0):
    docs = db.collection("materi_pancasila").limit(limit).offset(offset).stream()
    return [{"id": d.id, **d.to_dict()} for d in docs]

# --- CACHE DATA PENGERJAAN SISWA ---
@st.cache_data(ttl=5)
def get_user_pengerjaan_cached(username, limit=50):
    docs = db.collection("pengerjaan_siswa").where("username_siswa", "==", username).limit(limit).stream()
    return {d.to_dict().get("id_tugas"): {"id": d.id, **d.to_dict()} for d in docs}

@st.cache_data(ttl=5)
def get_pengerjaan_by_tugas_kelas_cached(tugas_id, kelas, limit=150, offset=0):
    docs = db.collection("pengerjaan_siswa").where("id_tugas", "==", tugas_id).where("kelas_siswa", "==", kelas).limit(limit).offset(offset).stream()
    return [{"id": d.id, **d.to_dict()} for d in docs]

@st.cache_data(ttl=5)
def get_all_pengerjaan_by_kelas_cached(kelas, limit=300):
    docs = db.collection("pengerjaan_siswa").where("kelas_siswa", "==", kelas).limit(limit).stream()
    return [d.to_dict() for d in docs]

# --- CACHE CLEAR HELPERS ---
def clear_kelas_cache(): 
    get_all_kelas.clear()
    get_guru_dashboard_stats.clear()

def clear_tugas_cache(): 
    get_all_tugas_cached.clear()
    get_guru_dashboard_stats.clear()

def clear_materi_cache(): 
    get_all_materi_cached.clear()
    get_guru_dashboard_stats.clear()

def clear_users_cache(): 
    get_all_users_cached.clear()
    get_users_paginated.clear()
    count_all_users.clear()
    get_siswa_by_kelas_cached.clear()
    count_siswa_by_kelas.clear()
    get_guru_dashboard_stats.clear()

def clear_pengerjaan_cache():
    get_user_pengerjaan_cached.clear()
    get_pengerjaan_by_tugas_kelas_cached.clear()
    get_all_pengerjaan_by_kelas_cached.clear()
    count_submitted_by_tugas_kelas.clear()
    get_guru_dashboard_stats.clear()

# ==========================================
# 3. HELPER ANALISIS BUTIR SOAL & REMEDIAL
# ==========================================
def hitung_analisis_butir_soal(soal_master, submitted_docs):
    """
    Menghitung Tingkat Kesukaran (P), Daya Beda (D), Distribusi Distraktor, dan Rekomendasi Butir Soal.
    """
    total_responden = len(submitted_docs)
    if total_responden == 0 or not soal_master:
        return [], {}

    siswa_scores = []
    for s in submitted_docs:
        un = s.get("username_siswa", "")
        ans = s.get("jawaban", [])
        score = s.get("nilai", 0)
        if score is None: score = 0
        siswa_scores.append({"username": un, "jawaban": ans, "score": score})

    siswa_scores.sort(key=lambda x: x["score"], reverse=True)

    n_group = max(1, int(round(total_responden * 0.27))) if total_responden >= 4 else max(1, total_responden // 2)
    upper_group = siswa_scores[:n_group]
    lower_group = siswa_scores[-n_group:]

    hasil_analisis = []

    for idx, q in enumerate(soal_master):
        q_text = q.get("pertanyaan", "") if isinstance(q, dict) else str(q)
        kunci_idx = q.get("kunci", 0) if isinstance(q, dict) else 0
        kunci_str = ['A', 'B', 'C', 'D'][kunci_idx] if 0 <= kunci_idx <= 3 else "A"

        count_opsi = {0: 0, 1: 0, 2: 0, 3: 0, -1: 0}
        benar_total = 0
        benar_upper = 0
        benar_lower = 0

        for s in siswa_scores:
            user_ans = s["jawaban"][idx] if idx < len(s["jawaban"]) else -1
            try: user_ans = int(user_ans)
            except (ValueError, TypeError): user_ans = -1

            if user_ans in count_opsi: count_opsi[user_ans] += 1
            else: count_opsi[-1] += 1

            if user_ans == kunci_idx: benar_total += 1

        for s in upper_group:
            u_ans = s["jawaban"][idx] if idx < len(s["jawaban"]) else -1
            try:
                if int(u_ans) == kunci_idx: benar_upper += 1
            except: pass

        for s in lower_group:
            l_ans = s["jawaban"][idx] if idx < len(s["jawaban"]) else -1
            try:
                if int(l_ans) == kunci_idx: benar_lower += 1
            except: pass

        p_val = round(benar_total / total_responden, 3)
        if p_val >= 0.85: p_ket = "Sangat Mudah"
        elif p_val >= 0.70: p_ket = "Mudah"
        elif p_val >= 0.30: p_ket = "Sedang"
        elif p_val >= 0.15: p_ket = "Sukar"
        else: p_ket = "Sangat Sukar"

        p_upper = benar_upper / n_group
        p_lower = benar_lower / n_group
        d_val = round(p_upper - p_lower, 3)

        if d_val >= 0.40: d_ket, d_status = "Sangat Baik", "Diterima"
        elif d_val >= 0.30: d_ket, d_status = "Baik", "Diterima"
        elif d_val >= 0.20: d_ket, d_status = "Cukup", "Direvisi"
        elif d_val >= 0.00: d_ket, d_status = "Jelek", "Dibuang/Revisi"
        else: d_ket, d_status = "Sangat Jelek", "Dibuang"

        distractor_info = []
        for opt_i in range(4):
            letter = ['A', 'B', 'C', 'D'][opt_i]
            cnt = count_opsi[opt_i]
            pct = round((cnt / total_responden) * 100, 1)
            if opt_i == kunci_idx:
                distractor_info.append(f"{letter}* ({cnt} / {pct}%)")
            else:
                eff = "Efektif" if pct >= 5.0 else "Kurang Efektif"
                distractor_info.append(f"{letter}: {cnt} ({pct}%) [{eff}]")

        if d_val >= 0.30 and (0.20 <= p_val <= 0.80): rekomendasi = "✅ Diterima (Soal Baik)"
        elif d_val >= 0.20: rekomendasi = "⚠️ Direvisi (Perlu Perbaikan)"
        else: rekomendasi = "❌ Dibuang / Diganti"

        hasil_analisis.append({
            "No": idx + 1,
            "Pertanyaan": q_text,
            "Kunci": kunci_str,
            "Benar": benar_total,
            "Tingkat Kesukaran (P)": p_val,
            "Kategori Kesukaran": p_ket,
            "Daya Beda (D)": d_val,
            "Kategori Daya Beda": d_ket,
            "Status Daya Beda": d_status,
            "Distraktor (A, B, C, D)": " | ".join(distractor_info),
            "Rekomendasi": rekomendasi,
            "opsi_counts": count_opsi,
            "n_upper_benar": benar_upper,
            "n_lower_benar": benar_lower
        })

    stats_ringkasan = {
        "total_responden": total_responden,
        "n_upper": n_group,
        "n_lower": n_group,
        "diterima": sum(1 for r in hasil_analisis if "Diterima" in r["Rekomendasi"]),
        "direvisi": sum(1 for r in hasil_analisis if "Direvisi" in r["Rekomendasi"]),
        "dibuang": sum(1 for r in hasil_analisis if "Dibuang" in r["Rekomendasi"])
    }

    return hasil_analisis, stats_ringkasan

# ==========================================
# 4. UTILITY & PAGINATION UI HELPERS
# ==========================================
def render_pagination_controls(total_items, default_page_size=10, key_prefix="pg"):
    if total_items <= 0: return 1, default_page_size, 0
    col_p1, col_p2, col_p3 = st.columns([2, 2, 4])
    with col_p1:
        page_size = st.selectbox("Tampilkan per Halaman", options=[5, 10, 20, 50, 100], index=[5, 10, 20, 50, 100].index(default_page_size) if default_page_size in [5, 10, 20, 50, 100] else 1, key=f"{key_prefix}_size")
    total_pages = max(1, (total_items + page_size - 1) // page_size)
    with col_p2:
        curr_page = st.number_input(f"Halaman (1 - {total_pages})", min_value=1, max_value=total_pages, value=1, step=1, key=f"{key_prefix}_num")
    offset = (curr_page - 1) * page_size
    with col_p3:
        start_item, end_item = offset + 1, min(offset + page_size, total_items)
        st.markdown(f"<p style='padding-top:25px; color:#666;'>Showing <b>{start_item}-{end_item}</b> of <b>{total_items}</b> items</p>", unsafe_allow_html=True)
    return curr_page, page_size, offset

def safe_read_uploaded_file(uploaded_file):
    if uploaded_file.name.endswith('.csv'):
        for enc in ['utf-8', 'utf-8-sig', 'cp1252', 'latin1']:
            try:
                uploaded_file.seek(0)
                return pd.read_csv(uploaded_file, encoding=enc)
            except (UnicodeDecodeError, UnicodeError): continue
        uploaded_file.seek(0)
        return pd.read_csv(uploaded_file, encoding='utf-8', errors='replace')
    return pd.read_excel(uploaded_file)

def hash_pass(password): return hashlib.sha256(password.encode()).hexdigest()

def generate_username(nama, existing_usernames=None):
    if existing_usernames is None: existing_usernames = {u["id"] for u in get_all_users_cached()}
    first_name = nama.strip().split()[0] if nama.strip() else "siswa"
    base_username = re.sub(r'[^a-z0-9]', '', first_name.lower())[:5] or "siswa"
    username, counter = base_username, 1
    while username in existing_usernames:
        username = f"{base_username}{counter}"
        counter += 1
    return username
    
def generate_password(length=6): return ''.join(random.choices(string.ascii_lowercase + string.digits, k=length))

def is_target_sesuai_kelas(doc_data, kelas_siswa):
    target = doc_data.get("target_kelas", [])
    if not target: return True
    return kelas_siswa in target if isinstance(target, list) else target == kelas_siswa

def submit_jawaban_siswa(tg, username_s, nama_s, kelas_s, answers=None, is_forced=False, is_violation=False):
    tg_id = tg["id"]
    soal_list = tg.get("soal", [])
    total_soal = len(soal_list)
    mode_kertas = tg.get("mode_kertas", False)
    
    if is_violation: catatan = "⚠️ Submit Otomatis (Limit Maksimal Pelanggaran)"
    elif is_forced: catatan = "Di-submit Paksa oleh Guru"
    elif mode_kertas: catatan = "Dikerjakan Manual di Lembar Kertas"
    else: catatan = "Penilaian Otomatis Sistem"
    
    doc_ref = db.collection("pengerjaan_siswa").document(f"{username_s}_{tg_id}")
    existing_snap = doc_ref.get()

    if (not answers) and existing_snap.exists:
        answers = existing_snap.to_dict().get("jawaban", [])

    if tg.get("tipe") == "pg":
        correct_count = 0
        formatted_ans = []
        for idx_q, sq in enumerate(soal_list):
            user_a = answers[idx_q] if (answers and idx_q < len(answers)) else None
            clean_ans = -1 if (user_a is None or user_a == "" or str(user_a) == "-1") else (int(user_a) if str(user_a).isdigit() else user_a)
            formatted_ans.append(clean_ans)
            
            kunci = sq.get("kunci")
            if not mode_kertas and clean_ans != -1 and kunci is not None:
                try:
                    if int(clean_ans) == int(kunci): correct_count += 1
                except (ValueError, TypeError):
                    if str(clean_ans).strip().lower() == str(kunci).strip().lower(): correct_count += 1
        
        score = None if mode_kertas else (round((correct_count / total_soal) * 100) if total_soal > 0 else 0)

        doc_ref.set({
            "id_tugas": tg_id, "judul_tugas": tg.get("judul"), "username_siswa": username_s,
            "nama_siswa": nama_s, "kelas_siswa": kelas_s, "tipe": "pg", "jawaban": formatted_ans,
            "nilai": score, "catatan_guru": catatan, "status": "submitted", "ijin_guru": True,
            "mode_kertas": mode_kertas, "submitted_at": firestore.SERVER_TIMESTAMP, "updated_at": firestore.SERVER_TIMESTAMP
        }, merge=True)
    else:
        formatted_ans = [a if a is not None else "" for a in (answers if answers else [])]
        doc_ref.set({
            "id_tugas": tg_id, "judul_tugas": tg.get("judul"), "username_siswa": username_s,
            "nama_siswa": nama_s, "kelas_siswa": kelas_s, "tipe": "essay", "soal": soal_list,
            "jawaban": formatted_ans, "nilai": None, "catatan_guru": catatan, "status": "submitted", "ijin_guru": True,
            "mode_kertas": mode_kertas, "submitted_at": firestore.SERVER_TIMESTAMP, "updated_at": firestore.SERVER_TIMESTAMP
        }, merge=True)

    clear_pengerjaan_cache()
    return True

def delete_tugas_and_submissions(tugas_id):
    batch = db.batch()
    batch.delete(db.collection("tugas_pancasila").document(tugas_id))
    for doc in db.collection("pengerjaan_siswa").where("id_tugas", "==", tugas_id).limit(100).stream():
        batch.delete(doc.reference)
    batch.commit()
    clear_tugas_cache()
    clear_pengerjaan_cache()

def reset_pengerjaan_siswa(username_siswa, tugas_id):
    db.collection("pengerjaan_siswa").document(f"{username_siswa}_{tugas_id}").delete()
    clear_pengerjaan_cache()
    return True

# ==========================================
# 5. AI EVALUATION HELPER
# ==========================================
def koreksi_essay_dengan_ai(soal_list, jawaban_list):
    api_key = st.secrets.get("GEMINI_API_KEY") or st.secrets.get("gemini", {}).get("api_key") or st.secrets.get("firebase", {}).get("GEMINI_API_KEY")
    if not api_key: return None, "⚠️ Key 'GEMINI_API_KEY' belum dikonfigurasi di secrets Streamlit."

    try:
        genai.configure(api_key=api_key)
        total_soal = len(soal_list)
        prompt_items = []
        for i in range(total_soal):
            s = soal_list[i] if i < len(soal_list) else ""
            j = jawaban_list[i] if i < len(jawaban_list) else ""
            q_text = s.get('pertanyaan', '') if isinstance(s, dict) else str(s)
            prompt_items.append(f"Soal {i+1}: {q_text}\nJawaban Siswa: {str(j).strip() or '(Tidak dijawab)'}")

        prompt = (f"Jumlah Soal: {total_soal}\n\n" + "\n\n".join(prompt_items) +
                  "\n\nKembalikan HANYA format JSON persis: {\"nilai\": 85, \"feedback\": \"Catatan...\"}")
        system_instruction = "Anda adalah Guru Pendidikan Pancasila. Evaluasi jawaban siswa secara objektif (skala 0-100)."

        for model_name in ['gemini-2.0-flash', 'gemini-1.5-flash']:
            try:
                model = genai.GenerativeModel(model_name=model_name, system_instruction=system_instruction)
                response = model.generate_content(prompt, generation_config={"response_mime_type": "application/json"})
                if response and hasattr(response, 'text') and response.text.strip():
                    raw_text = re.sub(r'^```json\s*|\s*```$', '', response.text.strip())
                    res_json = json.loads(raw_text)
                    return int(res_json.get("nilai", 0)), str(res_json.get("feedback", "")).strip()
            except Exception: continue

        return None, "Gagal memproses respon AI."
    except Exception as e:
        return None, f"Error AI: {str(e)}"

# ==========================================
# 6. AUTHENTICATION
# ==========================================
if "user" not in st.session_state: st.session_state["user"] = None

if ensure_default_admin_created(): st.toast("💡 Akun default awal berhasil dibuat! Username: admin | Pass: admin123")

if st.session_state["user"] is None:
    st.title("🇮🇩 LMS Pendidikan Pancasila")
    st.info("💡 **Informasi**: Akun Siswa dan Guru dikelola oleh **Super Admin**.")
    with st.form("form_login"):
        username = st.text_input("Username").strip().lower()
        password = st.text_input("Password", type="password")
        if st.form_submit_button("Masuk / Login"):
            if username and password:
                all_users = {u["id"]: u for u in get_all_users_cached(limit=500)}
                if username in all_users and all_users[username].get("password") == hash_pass(password):
                    u_data = all_users[username]
                    st.session_state["user"] = {
                        "username": username, "nama": u_data.get("nama"), "role": u_data.get("role"),
                        "kelas": u_data.get("kelas", ""), "kelas_ajar": u_data.get("kelas_ajar", [])
                    }
                    st.success(f"✅ Selamat datang, {u_data.get('nama')}!")
                    st.rerun()
                else: st.error("Username atau password salah!")
            else: st.warning("Isi username dan password.")
    st.stop()

# ==========================================
# 7. SIDEBAR & NAVIGATION
# ==========================================
user_info = st.session_state["user"]
role = user_info["role"]

st.sidebar.title(f"👋 Halo, {user_info['nama']}")
caption_text = f"Role: **{role.upper()}** | @{user_info['username']}"
if role == "siswa" and user_info.get("kelas"): caption_text += f"\n\n🏫 Kelas: **{user_info['kelas']}**"
elif role == "guru" and user_info.get("kelas_ajar"):
    k_str = ", ".join(user_info['kelas_ajar']) if isinstance(user_info['kelas_ajar'], list) else user_info['kelas_ajar']
    caption_text += f"\n\n🏫 Mengajar: **{k_str}**"
st.sidebar.caption(caption_text)

if st.sidebar.button("🚪 Keluar / Logout"):
    st.session_state.clear()
    components.html("<script>sessionStorage.clear(); localStorage.clear();</script>", height=0)
    st.rerun()

st.sidebar.divider()

# ==========================================
# 8. PANEL SUPER ADMIN
# ==========================================
def render_superadmin():
    st.title("⚙️ Panel Super Admin")
    t_kelas, t_list, t_add, t_imp, t_edit, t_del = st.tabs([
        "🏫 Master Kelas", "👥 Daftar User", "➕ Buat Akun", "📥 Import/Export", "✏️ Atur Kelas", "🗑️ Hapus Akun"
    ])

    with t_kelas:
        st.subheader("🏫 Kelola Master Data Kelas")
        daftar_kelas = get_all_kelas()
        col1, col2 = st.columns(2)
        with col1:
            for k in daftar_kelas: st.markdown(f"- 🏫 **{k}**")
        with col2:
            with st.form("form_add_k", clear_on_submit=True):
                new_k = st.text_input("Nama Kelas Baru").strip()
                if st.form_submit_button("Tambah Kelas"):
                    if new_k and new_k not in daftar_kelas:
                        db.collection("config").document("master_kelas").set({"daftar": sorted(daftar_kelas + [new_k])}, merge=True)
                        clear_kelas_cache()
                        st.success(f"✅ Kelas '{new_k}' ditambahkan!")
                        st.rerun()

    with t_list:
        st.subheader("👥 Daftar Akun (Pagination)")
        role_filter = st.selectbox("Filter Peran", ["semua", "siswa", "guru", "superadmin"], format_func=lambda x: x.upper())
        curr_page, limit, offset = render_pagination_controls(count_all_users(role_filter), default_page_size=10)
        p_users = get_users_paginated(limit=limit, offset=offset, role_filter=role_filter)
        st.dataframe(pd.DataFrame([{"Username": u.get("id"), "Nama": u.get("nama"), "Role": str(u.get("role")).upper(), "Kelas": u.get("kelas") or u.get("kelas_ajar") or "-"} for u in p_users]), use_container_width=True)

    with t_add:
        st.subheader("➕ Buat Akun Satuan")
        daftar_kelas = get_all_kelas()
        new_role = st.selectbox("Role Baru", ["Siswa", "Guru", "Superadmin"])
        with st.form("form_add_user", clear_on_submit=True):
            nama = st.text_input("Nama Lengkap")
            uname = st.text_input("Username").strip().lower()
            pwd = st.text_input("Password", type="password")
            k_siswa = st.selectbox("Pilih Kelas", options=daftar_kelas) if new_role == "Siswa" and daftar_kelas else None
            k_guru = st.multiselect("Pilih Kelas Ajar", options=daftar_kelas) if new_role == "Guru" and daftar_kelas else None
            if st.form_submit_button("Buat Akun"):
                if nama and uname and pwd:
                    payload = {"nama": nama, "password": hash_pass(pwd), "password_plain": pwd, "role": new_role.lower(), "created_at": firestore.SERVER_TIMESTAMP}
                    if new_role == "Siswa": payload["kelas"] = k_siswa
                    elif new_role == "Guru": payload["kelas_ajar"] = k_guru
                    db.collection("users").document(uname).set(payload)
                    clear_users_cache()
                    st.success(f"✅ Akun '{uname}' berhasil dibuat!")
                    st.rerun()

# ==========================================
# 9. PANEL GURU (DENGAN ANALISIS & PROGRAM REMEDIAL)
# ==========================================
def render_guru():
    st.title("🇮🇩 Panel Guru Pendidikan Pancasila")
    pilihan_kelas = user_info.get("kelas_ajar") or get_all_kelas()
    if isinstance(pilihan_kelas, str): pilihan_kelas = [pilihan_kelas]

    guru_stats = get_guru_dashboard_stats(tuple(pilihan_kelas))
    st.markdown("##### ⚡ Ringkasan Statistik Real-Time")
    m_col1, m_col2, m_col3, m_col4 = st.columns(4)
    m_col1.metric("👥 Total Siswa Ajar", f"{guru_stats['total_siswa']} Siswa")
    m_col2.metric("📝 Total Tugas Dibuat", f"{guru_stats['total_tugas']} Tugas")
    m_col3.metric("📖 Total Materi", f"{guru_stats['total_materi']} Modul")
    m_col4.metric("✅ Total Jawaban Masuk", f"{guru_stats['total_submitted']} Pengumpulan")
    st.divider()

    menu = st.sidebar.radio("📌 Menu Guru", ["📖 Kelola Materi", "📝 Buat & Kelola Tugas", "📊 Rekap, Analisis & Remedial", "📜 Daftar Nilai"])

    if menu == "📖 Kelola Materi":
        st.header("📖 Kelola Materi Pembelajaran")
        materi_docs = get_all_materi_cached(limit=50)
        for m in materi_docs:
            with st.container(border=True):
                st.markdown(f"### 📘 [{m.get('bab')}] {m.get('judul')}")
                if m.get("konten"): st.write(m.get("konten"))

    elif menu == "📝 Buat & Kelola Tugas":
        st.header("📝 Buat & Kelola Tugas")
        tugas_cached = get_all_tugas_cached(limit=50)
        for tg in tugas_cached:
            with st.expander(f"[{'PG' if tg.get('tipe')=='pg' else 'Essay'}] {tg.get('judul')}"):
                st.write(f"Instruksi: {tg.get('instruksi')}")

    elif menu == "📊 Rekap, Analisis & Remedial":
        st.header("📊 Rekap, Analisis Butir Soal & Program Remedial")
        if not pilihan_kelas: st.warning("⚠️ Anda belum ditugaskan mengajar kelas manapun."); st.stop()

        col_k, col_t = st.columns(2)
        with col_k: selected_kelas = st.selectbox("🏫 Pilih Kelas Ajar", options=pilihan_kelas)

        tugas_kelas = [d for d in get_all_tugas_cached(limit=50) if is_target_sesuai_kelas(d, selected_kelas)]
        if not tugas_kelas: st.info(f"Belum ada tugas untuk Kelas **{selected_kelas}**."); st.stop()

        with col_t:
            tg_options = {t["id"]: f"[{t.get('jenis_tugas', 'Ulangan Harian')}] {t.get('judul')}" for t in tugas_kelas}
            selected_tugas_id = st.selectbox("📝 Pilih Tugas/Ulangan", list(tg_options.keys()), format_func=lambda x: tg_options[x])
            selected_tugas = next(t for t in tugas_kelas if t["id"] == selected_tugas_id)

        siswa_list = get_siswa_by_kelas_cached(selected_kelas, limit=150)
        sub_list = get_pengerjaan_by_tugas_kelas_cached(selected_tugas_id, selected_kelas, limit=150)
        sub_map = {s.get("username_siswa"): s for s in sub_list}

        # TABS UTAMA EVALUASI
        t_rekap, t_koreksi, t_analisis, t_remedial, t_program_remed, t_kontrol, t_reset = st.tabs([
            "📋 Rekap Pengerjaan", 
            "✏️ Koreksi & Penilaian", 
            "📈 Analisis Butir Soal Lengkap", 
            "🎯 Daftar Ketuntasan & Remedial (KKM 70)", 
            "🛠️ Program Remedial & Pengayaan", 
            "🔓 Kontrol Izin", 
            "🔄 Reset Pengerjaan"
        ])

        # 1. TAB REKAP
        with t_rekap:
            rekap_rows = []
            for s in siswa_list:
                un = s["username"]
                sub = sub_map.get(un, {})
                rekap_rows.append({
                    "Username": un, "Nama Siswa": s.get("nama", un),
                    "Status": "✅ Sudah Submit" if sub.get("status") == "submitted" else "❌ Belum",
                    "Nilai": sub.get("nilai") if sub.get("nilai") is not None else "-",
                    "Catatan Guru": sub.get("catatan_guru", "-")
                })
            st.dataframe(pd.DataFrame(rekap_rows), use_container_width=True)

        # 2. TAB KOREKSI
        with t_koreksi:
            submitted_docs = [s for s in sub_list if s.get("status") == "submitted"]
            if not submitted_docs: st.info("Belum ada pengumpulan.")
            else:
                for sub in submitted_docs:
                    with st.expander(f"👤 {sub.get('nama_siswa')} — Nilai: {sub.get('nilai', 'Belum')}"):
                        st.write(f"Jawaban: {sub.get('jawaban')}")
                        if selected_tugas.get("tipe") == "essay" and st.button("🤖 Koreksi AI", key=f"ai_{sub['id']}"):
                            val, fb = koreksi_essay_dengan_ai(selected_tugas.get("soal", []), sub.get("jawaban", []))
                            if val is not None:
                                db.collection("pengerjaan_siswa").document(sub['id']).update({"nilai": val, "catatan_guru": fb})
                                clear_pengerjaan_cache()
                                st.success("✅ Nilai AI tersimpan!")
                                st.rerun()

        # 3. TAB ANALISIS BUTIR SOAL LENGKAP
        with t_analisis:
            st.subheader("📈 Analisis Butir Soal Lengkap (PG)")
            submitted_pg = [s for s in sub_list if s.get("status") == "submitted" and s.get("jawaban")]
            
            if selected_tugas.get("tipe") != "pg":
                st.info("ℹ️ Analisis butir soal ini khusus untuk tugas bertipe **Pilihan Ganda (PG)**.")
            elif not submitted_pg:
                st.info("ℹ️ Belum ada siswa yang mengumpulkan jawaban PG.")
            else:
                soal_master = selected_tugas.get("soal", [])
                hasil_analisis, stats_analisis = hitung_analisis_butir_soal(soal_master, submitted_pg)

                # Summary Cards
                c_a1, c_a2, c_a3, c_a4, c_a5 = st.columns(5)
                c_a1.metric("Total Responden", f"{stats_analisis['total_responden']} Siswa")
                c_a2.metric("Kelompok Atas/Bawah", f"{stats_analisis['n_upper']} Siswa")
                c_a3.metric("✅ Soal Diterima", f"{stats_analisis['diterima']} Soal")
                c_a4.metric("⚠️ Soal Direvisi", f"{stats_analisis['direvisi']} Soal")
                c_a5.metric("❌ Soal Dibuang", f"{stats_analisis['dibuang']} Soal")
                st.divider()

                # Table Display
                df_analisis = pd.DataFrame(hasil_analisis)
                display_cols = ["No", "Pertanyaan", "Kunci", "Benar", "Tingkat Kesukaran (P)", "Kategori Kesukaran", "Daya Beda (D)", "Kategori Daya Beda", "Status Daya Beda", "Rekomendasi"]
                st.dataframe(df_analisis[display_cols], use_container_width=True)

                # Detail per Soal
                st.subheader("🔍 Detail Sebaran Jawaban & Pengecoh (Distraktor)")
                selected_no = st.selectbox("Pilih Nomor Soal untuk Detail:", list(range(1, len(soal_master) + 1)), format_func=lambda x: f"Soal No. {x}")
                item_detail = hasil_analisis[selected_no - 1]

                col_d1, col_d2 = st.columns(2)
                with col_d1:
                    st.markdown(f"**Soal #{item_detail['No']}:** {item_detail['Pertanyaan']}")
                    st.markdown(f"**Kunci Jawaban:** Opsi {item_detail['Kunci']}")
                    st.write(f"**Distraktor & Pilihan:** {item_detail['Distraktor (A, B, C, D)']}")
                with col_d2:
                    st.info(f"**Tingkat Kesukaran ($P$):** {item_detail['Tingkat Kesukaran (P)']} ({item_detail['Kategori Kesukaran']})\n\n"
                            f"**Daya Beda ($D$):** {item_detail['Daya Beda (D)']} ({item_detail['Kategori Daya Beda']})\n\n"
                            f"**Rekomendasi Akhir:** {item_detail['Rekomendasi']}")

                st.download_button(
                    "💾 Unduh Rekap Analisis Butir Soal (.csv)",
                    df_analisis.to_csv(index=False).encode('utf-8-sig'),
                    f"analisis_butir_soal_{selected_kelas}_{selected_tugas_id}.csv",
                    "text/csv",
                    use_container_width=True
                )

        # 4. TAB DAFTAR KETUNTASAN & REMEDIAL (KKM 70)
        with t_remedial:
            st.subheader("🎯 Rekap Ketuntasan Belajar & Daftar Nama Remedial")
            kkm_val = st.number_input("Tentukan Nilai KKM (Kriteria Ketuntasan Minimal)", min_value=0, max_value=100, value=70, step=5)

            total_siswa = len(siswa_list)
            siswa_tuntas = []
            siswa_remedial = []

            for s in siswa_list:
                un = s["username"]
                nm = s.get("nama", un)
                sub = sub_map.get(un, {})
                nilai = sub.get("nilai")

                if sub.get("status") == "submitted" and nilai is not None:
                    if float(nilai) >= kkm_val:
                        siswa_tuntas.append({
                            "Username": un, "Nama Siswa": nm, "Nilai": nilai,
                            "Kelebihan Nilai": int(nilai - kkm_val), "Predikat": "Sangat Baik" if nilai >= 85 else "Baik",
                            "Keterangan": "🟢 TUNTAS (Mengikuti Pengayaan)"
                        })
                    else:
                        siswa_remedial.append({
                            "Username": un, "Nama Siswa": nm, "Nilai": nilai,
                            "Kekurangan Selisih KKM": int(kkm_val - nilai), "Status": "🔴 BELUM TUNTAS (REMEDIAL)",
                            "Rekomendasi Tindakan": "Mengikuti Ujian / Tugas Remedial"
                        })
                else:
                    siswa_remedial.append({
                        "Username": un, "Nama Siswa": nm, "Nilai": 0,
                        "Kekurangan Selisih KKM": kkm_val, "Status": "⏳ BELUM SUBMIT / MENGUMPULKAN",
                        "Rekomendasi Tindakan": "Mengikuti Tes Susulan / Remedial"
                    })

            n_tuntas = len(siswa_tuntas)
            n_remed = len(siswa_remedial)
            pct_tuntas = round((n_tuntas / total_siswa * 100), 1) if total_siswa > 0 else 0

            # Metric Cards
            r1, r2, r3, r4 = st.columns(4)
            r1.metric("Total Siswa Kelas", f"{total_siswa} Siswa")
            r2.metric("Siswa Tuntas (≥ KKM)", f"{n_tuntas} Siswa ({pct_tuntas}%)")
            r3.metric("Siswa Remedial (< KKM)", f"{n_remed} Siswa ({round(100 - pct_tuntas, 1)}%)")
            r4.metric("Status Ketuntasan Klasikal", "✅ TUNTAS" if pct_tuntas >= 75 else "🔴 BELUM TUNTAS")
            st.divider()

            # Tabel Remedial
            st.markdown(f"### 🔴 Daftar Siswa Remedial (Nilai < {kkm_val} / Belum Mengumpulkan)")
            if siswa_remedial:
                df_remed = pd.DataFrame(siswa_remedial)
                st.dataframe(df_remed, use_container_width=True)
            else:
                st.success("🎉 Selamat! Seluruh siswa telah tuntas mencapai KKM.")

            # Tabel Tuntas
            st.markdown(f"### 🟢 Daftar Siswa Tuntas (Nilai ≥ {kkm_val})")
            if siswa_tuntas:
                df_tuntas = pd.DataFrame(siswa_tuntas)
                st.dataframe(df_tuntas, use_container_width=True)

            # Download Recap Button
            recap_combine = pd.concat([
                pd.DataFrame(siswa_remedial).assign(Kelompok="REMEDIAL"),
                pd.DataFrame(siswa_tuntas).assign(Kelompok="TUNTAS")
            ], ignore_index=True)
            st.download_button("💾 Unduh Rekap Ketuntasan & Remedial (.csv)", recap_combine.to_csv(index=False).encode('utf-8-sig'), f"rekap_remedial_kkm{kkm_val}_{selected_kelas}.csv", "text/csv", use_container_width=True)

        # 5. TAB PROGRAM REMEDIAL & PENGAYAAN
        with t_program_remed:
            st.subheader("🛠️ Program Execution Remedial & Pengayaan")
            kkm_prog = 70
            remed_users = [s["username"] for s in siswa_list if sub_map.get(s["username"], {}).get("nilai") is None or float(sub_map.get(s["username"], {}).get("nilai", 0)) < kkm_prog]
            pct_klasikal = round((len(siswa_list) - len(remed_users)) / len(siswa_list) * 100, 1) if siswa_list else 0

            # Rekomendasi Pelaksanaan
            st.markdown("##### 📌 Rekomendasi Strategi Pelaksanaan Remedial")
            if pct_klasikal < 50:
                st.error(f"⚠️ **Ketuntasan Klasikal Rendah ({pct_klasikal}%)**: Disarankan melakukan **Pembelajaran Ulang Klasikal** untuk seluruh kelas sebelum Tes Ulang Remedial.")
            elif pct_klasikal < 75:
                st.warning(f"⚡ **Ketuntasan Klasikal Sedang ({pct_klasikal}%)**: Disarankan memberikan **Bimbingan Kelompok / Tutor Sebaya** bagi siswa remedial.")
            else:
                st.success(f"🎉 **Ketuntasan Klasikal Tinggi ({pct_klasikal}%)**: Cukup berikan **Bimbingan Perorangan & Penugasan Remedial Mandiri**.")

            st.divider()

            # Eksekusi Reset Remedial
            st.markdown("### ⚡ Aksi Cepat Remedial")
            col_act1, col_act2 = st.columns(2)
            with col_act1:
                if st.button("🔄 Reset Pengerjaan Seluruh Siswa Remedial", type="primary", use_container_width=True):
                    for un_r in remed_users:
                        reset_pengerjaan_siswa(un_r, selected_tugas_id)
                    st.success(f"✅ Berhasil mereset status {len(remed_users)} siswa remedial agar dapat mengerjakan ulang!")
                    st.rerun()

            with col_act2:
                st.info("Siswa yang di-reset dapat langsung mengerjakan ujian kembali dari Dashboard Siswa.")

            st.divider()

            # Form Penyusunan Laporan Program Remedial
            st.markdown("### 📝 Form Rencana & Laporan Pelaksanaan Program Remedial")
            with st.form("form_laporan_remedial"):
                tgl_remed = st.date_input("Tanggal Pelaksanaan Remedial", datetime.now())
                materi_remed = st.text_input("Materi Utama / Indikator yang Di-remedialkan", value=f"Indikator pada {selected_tugas.get('judul')}")
                bentuk_kegiatan = st.selectbox("Bentuk Kegiatan Remedial", ["Pembelajaran Ulang Klasikal", "Bimbingan Kelompok", "Tutor Sebaya", "Tugas Mandiri / Penugasan Khusus"])
                target_remed = st.number_input("Target Nilai Setelah Remedial", min_value=70, max_value=100, value=75)

                if st.form_submit_button("📄 Cetak Dokumen Program Remedial"):
                    doc_report = f"""
=====================================================
LAPORAN PELAKSANAAN PROGRAM REMEDIAL & PENGAYAAN
=====================================================
Mata Pelajaran  : Pendidikan Pancasila
Kelas / Tugas   : {selected_kelas} / {selected_tugas.get('judul')}
Tanggal Remedial: {tgl_remed.strftime('%d-%m-%Y')}
Bentuk Kegiatan : {bentuk_kegiatan}
Materi Remedial : {materi_remed}
Target Nilai    : {target_remed}

DAFTAR SISWA REMEDIAL ({len(remed_users)} Siswa):
-----------------------------------------------------
"""
                    for idx_r, un_r in enumerate(remed_users, 1):
                        nm_r = sub_map.get(un_r, {}).get("nama_siswa", un_r)
                        n_awal = sub_map.get(un_r, {}).get("nilai", 0)
                        doc_report += f"{idx_r}. {nm_r} (@{un_r}) | Nilai Awal: {n_awal} | Target: {target_remed}\n"

                    st.download_button("💾 Unduh Dokumen Laporan Program Remedial (.txt)", doc_report.encode('utf-8'), f"laporan_program_remedial_{selected_kelas}.txt", "text/plain")

        # 6. TAB KONTROL IZIN
        with t_kontrol:
            st.subheader("🔓 Kontrol Izin & Buka Kunci Siswa")
            locked_students = [s for s in siswa_list if sub_map.get(s["username"], {}).get("status") == "in_progress" and not sub_map.get(s["username"], {}).get("ijin_guru", True)]
            if not locked_students: st.info("ℹ️ Tidak ada siswa yang terkunci.")
            else:
                for ls in locked_students:
                    if st.button(f"🔓 Beri Izin {ls.get('nama')}", key=f"btn_grant_{ls['username']}"):
                        db.collection("pengerjaan_siswa").document(f"{ls['username']}_{selected_tugas_id}").set({"ijin_guru": True}, merge=True)
                        clear_pengerjaan_cache()
                        st.rerun()

        # 7. TAB RESET PENGERJAAN
        with t_reset:
            st.subheader("🔄 Reset Pengerjaan Siswa")
            siswa_pengerjaan = [s for s in siswa_list if s["username"] in sub_map]
            if siswa_pengerjaan:
                sel_indiv = st.selectbox("Pilih Siswa Reset", [s["username"] for s in siswa_pengerjaan])
                if st.button("🗑️ Reset Siswa Ini", type="primary"):
                    reset_pengerjaan_siswa(sel_indiv, selected_tugas_id)
                    st.success("✅ Berhasil di-reset!")
                    st.rerun()

    elif menu == "📜 Daftar Nilai":
        st.header("📜 Transkrip & Daftar Nilai Siswa")
        selected_kelas = st.selectbox("🏫 Pilih Kelas", options=pilihan_kelas)
        tugas_kelas = [d for d in get_all_tugas_cached(limit=50) if is_target_sesuai_kelas(d, selected_kelas)]
        siswa_list = get_siswa_by_kelas_cached(selected_kelas, limit=150)
        sub_list = get_all_pengerjaan_by_kelas_cached(selected_kelas, limit=300)
        sub_map = {(s.get("username_siswa"), s.get("id_tugas")): s for s in sub_list}

        table_rows = []
        for s in siswa_list:
            un = s["username"]
            row = {"Username": un, "Nama Siswa": s.get("nama", un)}
            scores = []
            for tg in tugas_kelas:
                sub = sub_map.get((un, tg["id"]), {})
                val = sub.get("nilai")
                if sub.get("status") == "submitted" and val is not None:
                    row[tg.get("judul")] = val
                    scores.append(float(val))
                else: row[tg.get("judul")] = "-"
            row["Rata-Rata"] = round(sum(scores)/len(scores), 2) if scores else "-"
            table_rows.append(row)

        df_nilai = pd.DataFrame(table_rows)
        st.dataframe(df_nilai, use_container_width=True)
        st.download_button("💾 Unduh Rekap Nilai (.csv)", df_nilai.to_csv(index=False).encode('utf-8-sig'), f"rekap_nilai_{selected_kelas}.csv", "text/csv")

# ==========================================
# 10. PANEL SISWA (CBT INTERFACE)
# ==========================================
def render_siswa():
    kelas_s = user_info.get("kelas", "-")
    nama_s = user_info.get("nama", "Siswa")
    username_s = user_info.get("username", "")

    my_subs = get_user_pengerjaan_cached(username_s)
    active_quiz_id = st.session_state.get("active_quiz_id")

    if active_quiz_id:
        tg = next((t for t in get_all_tugas_cached(limit=100) if t["id"] == active_quiz_id), None)
        if not tg: st.session_state["active_quiz_id"] = None; st.rerun()

        st.markdown(f"## 📝 {tg.get('judul')}")
        if st.button("⬅️ Kembali ke Dashboard"):
            st.session_state["active_quiz_id"] = None
            st.rerun()

        soal_list = tg.get("soal", [])
        with st.form(key=f"cbt_form_{tg['id']}"):
            jawaban_user = []
            for idx, sq in enumerate(soal_list):
                st.markdown(f"#### **{idx + 1}. {sq.get('pertanyaan', '')}**")
                if tg.get("tipe") == "pg":
                    opsi = sq.get("opsi", ["A", "B", "C", "D"])
                    ans = st.radio(f"Pilih Jawaban #{idx+1}:", options=list(range(len(opsi))), format_func=lambda x: f"{['A','B','C','D'][x]}. {opsi[x]}", key=f"ans_{idx}", index=None)
                    jawaban_user.append(ans if ans is not None else -1)
                else:
                    ans = st.text_area(f"Jawaban Essay #{idx+1}:", key=f"essay_{idx}")
                    jawaban_user.append(ans)
                st.divider()

            if st.form_submit_button("🚀 Kirim Jawaban", type="primary", use_container_width=True):
                submit_jawaban_siswa(tg, username_s, nama_s, kelas_s, answers=jawaban_user)
                st.session_state["active_quiz_id"] = None
                clear_pengerjaan_cache()
                st.success("✅ Jawaban berhasil dikirim!")
                st.rerun()
        st.stop()

    st.markdown(f"""
        <div class="student-header">
            <h2>🇮🇩 Dashboard Siswa Pendidikan Pancasila</h2>
            <p>Selamat Datang, <b>{nama_s}</b> | Kelas: <b>{kelas_s}</b></p>
        </div>
    """, unsafe_allow_html=True)

    t_materi_s, t_tugas_s = st.tabs(["📖 Materi Pembelajaran", "📝 Tugas & Ujian Saya"])

    with t_materi_s:
        for m in [m for m in get_all_materi_cached(limit=50) if is_target_sesuai_kelas(m, kelas_s)]:
            with st.container(border=True):
                st.markdown(f"### 📘 [{m.get('bab')}] {m.get('judul')}")
                if m.get("konten"): st.write(m.get("konten"))

    with t_tugas_s:
        tugas_siswa = [t for t in get_all_tugas_cached(limit=50) if is_target_sesuai_kelas(t, kelas_s) and t.get("status", "terbit") == "terbit"]
        for tg in tugas_siswa:
            sub = my_subs.get(tg["id"], {})
            status_sub = sub.get("status", "belum")
            with st.container(border=True):
                col_i, col_b = st.columns([3, 1])
                with col_i:
                    st.markdown(f"### 📝 {tg.get('judul')}")
                    if status_sub == "submitted":
                        st.success(f"✅ Sudah Dikumpulkan | Nilai: {sub.get('nilai', 'Belum Dinilai')}")
                with col_b:
                    if status_sub != "submitted":
                        if st.button("Mulai Ujian 🚀", key=f"start_{tg['id']}", use_container_width=True):
                            st.session_state["active_quiz_id"] = tg["id"]
                            st.rerun()

# ==========================================
# 11. MAIN ROUTER
# ==========================================
if role == "superadmin": render_superadmin()
elif role == "guru": render_guru()
elif role == "siswa": render_siswa()
else: st.error("Role tidak dikenal.")
update dan upgrade app sehingga terdapat kisi-kisi soal (bisa di print)

Pembaruan untuk fitur cetak Kisi-Kisi Soal telah ditambahkan ke dalam aplikasi!

Fitur ini dapat diakses pada panel Guru di menu "📝 Buat & Kelola Tugas" > tab "🖨️ Cetak Kisi-Kisi".

Berikut adalah file app.py yang telah diperbarui dan siap digunakan:

PY icon
app
 PY 
Perubahan utama yang dimasukkan dalam tab Kisi-Kisi ini meliputi:

Tabel Interaktif: Menampilkan nomor soal, pertanyaan, pilihan ganda (jika ada), dan kunci jawaban secara terstruktur.

Unduh Otomatis: Tombol unduh untuk mengekspor kisi-kisi soal dalam format CSV yang kompatibel dan siap dicetak/diedit lebih lanjut di Excel.





Your Kementerian Pendidikan dan Kebudayaan Indonesia (SMP) chats aren’t used to improve our models. Gemini is AI and can make mistakes. Your privacy & GeminiOpens in a new window

Processed and analyzed in code
import os
import json
import io
import re
import random
import string
import hashlib
import time
import base64
import urllib.parse
from datetime import datetime

import streamlit as st
import streamlit.components.v1 as components
import firebase_admin
from firebase_admin import credentials, firestore
import pandas as pd
import google.generativeai as genai

# Impor FirestoreBundle untuk penanganan Firestore Data Bundles
try:
    from google.cloud.firestore_bundle import FirestoreBundle
except ImportError:
    try:
        from google.cloud.firestore_v1.bundle import FirestoreBundle
    except ImportError:
        FirestoreBundle = None

# ==========================================
# 1. CONFIG & STYLING
# ==========================================
st.set_page_config(
    page_title="LMS Pendidikan Pancasila",
    page_icon="🇮🇩",
    layout="wide",
    initial_sidebar_state="collapsed"
)

st.markdown("""
    <style>
    html, body, [class*="css"] {
        font-family: -apple-system, BlinkMacSystemFont, "Segoe UI", Roboto, Helvetica, Arial, sans-serif;
    }
    input[type="text"], input[type="password"], textarea, select { 
        font-size: 16px !important;
        border-radius: 10px !important;
    }
    @media (max-width: 768px) {
        .main .block-container { padding: 0.8rem 0.6rem 3rem !important; }
        [data-testid="column"] { width: 100% !important; flex: 1 1 100% !important; min-width: 100% !important; margin-bottom: 0.5rem; }
        .stButton > button, .stDownloadButton > button { 
            width: 100% !important; min-height: 50px !important; font-size: 16px !important; font-weight: bold; border-radius: 12px !important; 
        }
        h1 { font-size: 1.6rem !important; } 
        h2 { font-size: 1.3rem !important; } 
        h3 { font-size: 1.1rem !important; }
    }
    .student-header {
        background: linear-gradient(135deg, #1e3c72 0%, #2a5298 100%);
        color: white; padding: 18px 20px; border-radius: 16px; margin-bottom: 15px;
    }
    .stTabs [data-baseweb="tab-list"] { gap: 6px; overflow-x: auto; white-space: nowrap; border-bottom: 2px solid #eaeaea; padding-bottom: 4px; }
    .stTabs [data-baseweb="tab"] { padding: 10px 18px; border-radius: 20px; font-weight: 600; }
    .stTabs [aria-selected="true"] { background-color: #1e3c72 !important; color: white !important; }
    
    .cbt-container {
        background-color: #ffffff;
        padding: 24px;
        border-radius: 16px;
        box-shadow: 0 4px 15px rgba(0,0,0,0.05);
        border: 1px solid #e1e8ed;
        margin-bottom: 20px;
    }
    .soal-box {
        background-color: #f8fafc;
        border: 1px solid #e2e8f0;
        border-radius: 12px;
        padding: 18px;
        margin-bottom: 20px;
    }
    </style>
""", unsafe_allow_html=True)

# ==========================================
# 2. FIREBASE & CACHING OPTIMIZATION
# ==========================================
@st.cache_resource
def init_firebase():
    if not firebase_admin._apps:
        key_dict = dict(st.secrets["firebase"])
        if "\\n" in key_dict["private_key"]:
            key_dict["private_key"] = key_dict["private_key"].replace("\\n", "\n")
        cred = credentials.Certificate(key_dict)
        firebase_admin.initialize_app(cred)
    return firestore.client()

try:
    db = init_firebase()
except Exception as e:
    st.error(f"Gagal terhubung ke Firebase: {e}")
    st.stop()

# --- FIRESTORE AGGREGATION QUERY HELPERS ---
@st.cache_data(ttl=60)
def count_siswa_by_kelas(kelas):
    try:
        query = db.collection("users").where("role", "==", "siswa").where("kelas", "==", kelas)
        res = query.count().get()
        return res[0][0].value
    except Exception:
        return 0

@st.cache_data(ttl=60)
def count_all_users(role_filter=None):
    try:
        query = db.collection("users")
        if role_filter and role_filter != "semua":
            query = query.where("role", "==", role_filter)
        res = query.count().get()
        return res[0][0].value
    except Exception:
        return 0

@st.cache_data(ttl=60)
def count_submitted_by_tugas_kelas(tugas_id, kelas):
    try:
        query = db.collection("pengerjaan_siswa").where("id_tugas", "==", tugas_id).where("kelas_siswa", "==", kelas).where("status", "==", "submitted")
        res = query.count().get()
        return res[0][0].value
    except Exception:
        return 0

@st.cache_data(ttl=60)
def get_guru_dashboard_stats(pilihan_kelas_tuple):
    try:
        total_materi = db.collection("materi_pancasila").count().get()[0][0].value
    except Exception:
        total_materi = 0

    try:
        total_tugas = db.collection("tugas_pancasila").count().get()[0][0].value
    except Exception:
        total_tugas = 0

    total_siswa = 0
    for k in pilihan_kelas_tuple:
        total_siswa += count_siswa_by_kelas(k)

    total_submitted = 0
    for k in pilihan_kelas_tuple:
        try:
            q_sub = db.collection("pengerjaan_siswa").where("kelas_siswa", "==", k).where("status", "==", "submitted")
            total_submitted += q_sub.count().get()[0][0].value
        except Exception:
            pass

    return {
        "total_siswa": total_siswa,
        "total_tugas": total_tugas,
        "total_materi": total_materi,
        "total_submitted": total_submitted
    }

# --- OPTIMIZED CACHED READ FUNCTIONS ---
@st.cache_data(ttl=86400)
def ensure_default_admin_created():
    admin_ref = db.collection("users").document("admin")
    if not admin_ref.get().exists:
        admin_ref.set({
            "nama": "Super Admin",
            "role": "superadmin",
            "password": hash_pass("admin123"),
            "password_plain": "admin123",
            "created_at": firestore.SERVER_TIMESTAMP
        })
        return True
    return False

@st.cache_data(ttl=86400)
def get_all_kelas():
    doc = db.collection("config").document("master_kelas").get()
    if doc.exists:
        return sorted(doc.to_dict().get("daftar", []))
    return []

@st.cache_data(ttl=300)
def get_users_paginated(limit=10, offset=0, role_filter=None):
    query = db.collection("users")
    if role_filter and role_filter != "semua":
        query = query.where("role", "==", role_filter)
    query = query.limit(limit).offset(offset)
    docs = query.stream()
    return [{"id": d.id, **d.to_dict()} for d in docs]

@st.cache_data(ttl=600)
def get_all_users_cached(limit=500):
    docs = db.collection("users").limit(limit).stream()
    return [{"id": d.id, **d.to_dict()} for d in docs]

@st.cache_data(ttl=300)
def get_siswa_by_kelas_cached(kelas, limit=150, offset=0):
    docs = db.collection("users").where("role", "==", "siswa").where("kelas", "==", kelas).limit(limit).offset(offset).stream()
    return [{"username": d.id, **d.to_dict()} for d in docs]

@st.cache_data(ttl=300)
def get_all_tugas_cached(limit=100, offset=0):
    docs = db.collection("tugas_pancasila").limit(limit).offset(offset).stream()
    return [{"id": d.id, **d.to_dict()} for d in docs]

@st.cache_data(ttl=300)
def get_all_materi_cached(limit=100, offset=0):
    docs = db.collection("materi_pancasila").limit(limit).offset(offset).stream()
    return [{"id": d.id, **d.to_dict()} for d in docs]

# --- CACHE DATA PENGERJAAN SISWA ---
@st.cache_data(ttl=5)
def get_user_pengerjaan_cached(username, limit=50):
    docs = db.collection("pengerjaan_siswa").where("username_siswa", "==", username).limit(limit).stream()
    return {d.to_dict().get("id_tugas"): {"id": d.id, **d.to_dict()} for d in docs}

@st.cache_data(ttl=5)
def get_pengerjaan_by_tugas_kelas_cached(tugas_id, kelas, limit=150, offset=0):
    docs = db.collection("pengerjaan_siswa").where("id_tugas", "==", tugas_id).where("kelas_siswa", "==", kelas).limit(limit).offset(offset).stream()
    return [{"id": d.id, **d.to_dict()} for d in docs]

@st.cache_data(ttl=5)
def get_all_pengerjaan_by_kelas_cached(kelas, limit=300):
    docs = db.collection("pengerjaan_siswa").where("kelas_siswa", "==", kelas).limit(limit).stream()
    return [d.to_dict() for d in docs]

# --- CACHE CLEAR HELPERS ---
def clear_kelas_cache(): 
    get_all_kelas.clear()
    get_guru_dashboard_stats.clear()

def clear_tugas_cache(): 
    get_all_tugas_cached.clear()
    get_guru_dashboard_stats.clear()

def clear_materi_cache(): 
    get_all_materi_cached.clear()
    get_guru_dashboard_stats.clear()

def clear_users_cache(): 
    get_all_users_cached.clear()
    get_users_paginated.clear()
    count_all_users.clear()
    get_siswa_by_kelas_cached.clear()
    count_siswa_by_kelas.clear()
    get_guru_dashboard_stats.clear()

def clear_pengerjaan_cache():
    get_user_pengerjaan_cached.clear()
    get_pengerjaan_by_tugas_kelas_cached.clear()
    get_all_pengerjaan_by_kelas_cached.clear()
    count_submitted_by_tugas_kelas.clear()
    get_guru_dashboard_stats.clear()

# ==========================================
# 3. HELPER ANALISIS BUTIR SOAL & REMEDIAL
# ==========================================
def hitung_analisis_butir_soal(soal_master, submitted_docs):
    """
    Menghitung Tingkat Kesukaran (P), Daya Beda (D), Distribusi Distraktor, dan Rekomendasi Butir Soal.
    """
    total_responden = len(submitted_docs)
    if total_responden == 0 or not soal_master:
        return [], {}

    siswa_scores = []
    for s in submitted_docs:
        un = s.get("username_siswa", "")
        ans = s.get("jawaban", [])
        score = s.get("nilai", 0)
        if score is None: score = 0
        siswa_scores.append({"username": un, "jawaban": ans, "score": score})

    siswa_scores.sort(key=lambda x: x["score"], reverse=True)

    n_group = max(1, int(round(total_responden * 0.27))) if total_responden >= 4 else max(1, total_responden // 2)
    upper_group = siswa_scores[:n_group]
    lower_group = siswa_scores[-n_group:]

    hasil_analisis = []

    for idx, q in enumerate(soal_master):
        q_text = q.get("pertanyaan", "") if isinstance(q, dict) else str(q)
        kunci_idx = q.get("kunci", 0) if isinstance(q, dict) else 0
        kunci_str = ['A', 'B', 'C', 'D'][kunci_idx] if 0 <= kunci_idx <= 3 else "A"

        count_opsi = {0: 0, 1: 0, 2: 0, 3: 0, -1: 0}
        benar_total = 0
        benar_upper = 0
        benar_lower = 0

        for s in siswa_scores:
            user_ans = s["jawaban"][idx] if idx < len(s["jawaban"]) else -1
            try: user_ans = int(user_ans)
            except (ValueError, TypeError): user_ans = -1

            if user_ans in count_opsi: count_opsi[user_ans] += 1
            else: count_opsi[-1] += 1

            if user_ans == kunci_idx: benar_total += 1

        for s in upper_group:
            u_ans = s["jawaban"][idx] if idx < len(s["jawaban"]) else -1
            try:
                if int(u_ans) == kunci_idx: benar_upper += 1
            except: pass

        for s in lower_group:
            l_ans = s["jawaban"][idx] if idx < len(s["jawaban"]) else -1
            try:
                if int(l_ans) == kunci_idx: benar_lower += 1
            except: pass

        p_val = round(benar_total / total_responden, 3)
        if p_val >= 0.85: p_ket = "Sangat Mudah"
        elif p_val >= 0.70: p_ket = "Mudah"
        elif p_val >= 0.30: p_ket = "Sedang"
        elif p_val >= 0.15: p_ket = "Sukar"
        else: p_ket = "Sangat Sukar"

        p_upper = benar_upper / n_group
        p_lower = benar_lower / n_group
        d_val = round(p_upper - p_lower, 3)

        if d_val >= 0.40: d_ket, d_status = "Sangat Baik", "Diterima"
        elif d_val >= 0.30: d_ket, d_status = "Baik", "Diterima"
        elif d_val >= 0.20: d_ket, d_status = "Cukup", "Direvisi"
        elif d_val >= 0.00: d_ket, d_status = "Jelek", "Dibuang/Revisi"
        else: d_ket, d_status = "Sangat Jelek", "Dibuang"

        distractor_info = []
        for opt_i in range(4):
            letter = ['A', 'B', 'C', 'D'][opt_i]
            cnt = count_opsi[opt_i]
            pct = round((cnt / total_responden) * 100, 1)
            if opt_i == kunci_idx:
                distractor_info.append(f"{letter}* ({cnt} / {pct}%)")
            else:
                eff = "Efektif" if pct >= 5.0 else "Kurang Efektif"
                distractor_info.append(f"{letter}: {cnt} ({pct}%) [{eff}]")

        if d_val >= 0.30 and (0.20 <= p_val <= 0.80): rekomendasi = "✅ Diterima (Soal Baik)"
        elif d_val >= 0.20: rekomendasi = "⚠️ Direvisi (Perlu Perbaikan)"
        else: rekomendasi = "❌ Dibuang / Diganti"

        hasil_analisis.append({
            "No": idx + 1,
            "Pertanyaan": q_text,
            "Kunci": kunci_str,
            "Benar": benar_total,
            "Tingkat Kesukaran (P)": p_val,
            "Kategori Kesukaran": p_ket,
            "Daya Beda (D)": d_val,
            "Kategori Daya Beda": d_ket,
            "Status Daya Beda": d_status,
            "Distraktor (A, B, C, D)": " | ".join(distractor_info),
            "Rekomendasi": rekomendasi,
            "opsi_counts": count_opsi,
            "n_upper_benar": benar_upper,
            "n_lower_benar": benar_lower
        })

    stats_ringkasan = {
        "total_responden": total_responden,
        "n_upper": n_group,
        "n_lower": n_group,
        "diterima": sum(1 for r in hasil_analisis if "Diterima" in r["Rekomendasi"]),
        "direvisi": sum(1 for r in hasil_analisis if "Direvisi" in r["Rekomendasi"]),
        "dibuang": sum(1 for r in hasil_analisis if "Dibuang" in r["Rekomendasi"])
    }

    return hasil_analisis, stats_ringkasan

# ==========================================
# 4. UTILITY & PAGINATION UI HELPERS
# ==========================================
def render_pagination_controls(total_items, default_page_size=10, key_prefix="pg"):
    if total_items <= 0: return 1, default_page_size, 0
    col_p1, col_p2, col_p3 = st.columns([2, 2, 4])
    with col_p1:
        page_size = st.selectbox("Tampilkan per Halaman", options=[5, 10, 20, 50, 100], index=[5, 10, 20, 50, 100].index(default_page_size) if default_page_size in [5, 10, 20, 50, 100] else 1, key=f"{key_prefix}_size")
    total_pages = max(1, (total_items + page_size - 1) // page_size)
    with col_p2:
        curr_page = st.number_input(f"Halaman (1 - {total_pages})", min_value=1, max_value=total_pages, value=1, step=1, key=f"{key_prefix}_num")
    offset = (curr_page - 1) * page_size
    with col_p3:
        start_item, end_item = offset + 1, min(offset + page_size, total_items)
        st.markdown(f"<p style='padding-top:25px; color:#666;'>Showing <b>{start_item}-{end_item}</b> of <b>{total_items}</b> items</p>", unsafe_allow_html=True)
    return curr_page, page_size, offset

def safe_read_uploaded_file(uploaded_file):
    if uploaded_file.name.endswith('.csv'):
        for enc in ['utf-8', 'utf-8-sig', 'cp1252', 'latin1']:
            try:
                uploaded_file.seek(0)
                return pd.read_csv(uploaded_file, encoding=enc)
            except (UnicodeDecodeError, UnicodeError): continue
        uploaded_file.seek(0)
        return pd.read_csv(uploaded_file, encoding='utf-8', errors='replace')
    return pd.read_excel(uploaded_file)

def hash_pass(password): return hashlib.sha256(password.encode()).hexdigest()

def generate_username(nama, existing_usernames=None):
    if existing_usernames is None: existing_usernames = {u["id"] for u in get_all_users_cached()}
    first_name = nama.strip().split()[0] if nama.strip() else "siswa"
    base_username = re.sub(r'[^a-z0-9]', '', first_name.lower())[:5] or "siswa"
    username, counter = base_username, 1
    while username in existing_usernames:
        username = f"{base_username}{counter}"
        counter += 1
    return username
    
def generate_password(length=6): return ''.join(random.choices(string.ascii_lowercase + string.digits, k=length))

def is_target_sesuai_kelas(doc_data, kelas_siswa):
    target = doc_data.get("target_kelas", [])
    if not target: return True
    return kelas_siswa in target if isinstance(target, list) else target == kelas_siswa

def submit_jawaban_siswa(tg, username_s, nama_s, kelas_s, answers=None, is_forced=False, is_violation=False):
    tg_id = tg["id"]
    soal_list = tg.get("soal", [])
    total_soal = len(soal_list)
    mode_kertas = tg.get("mode_kertas", False)
    
    if is_violation: catatan = "⚠️ Submit Otomatis (Limit Maksimal Pelanggaran)"
    elif is_forced: catatan = "Di-submit Paksa oleh Guru"
    elif mode_kertas: catatan = "Dikerjakan Manual di Lembar Kertas"
    else: catatan = "Penilaian Otomatis Sistem"
    
    doc_ref = db.collection("pengerjaan_siswa").document(f"{username_s}_{tg_id}")
    existing_snap = doc_ref.get()

    if (not answers) and existing_snap.exists:
        answers = existing_snap.to_dict().get("jawaban", [])

    if tg.get("tipe") == "pg":
        correct_count = 0
        formatted_ans = []
        for idx_q, sq in enumerate(soal_list):
            user_a = answers[idx_q] if (answers and idx_q < len(answers)) else None
            clean_ans = -1 if (user_a is None or user_a == "" or str(user_a) == "-1") else (int(user_a) if str(user_a).isdigit() else user_a)
            formatted_ans.append(clean_ans)
            
            kunci = sq.get("kunci")
            if not mode_kertas and clean_ans != -1 and kunci is not None:
                try:
                    if int(clean_ans) == int(kunci): correct_count += 1
                except (ValueError, TypeError):
                    if str(clean_ans).strip().lower() == str(kunci).strip().lower(): correct_count += 1
        
        score = None if mode_kertas else (round((correct_count / total_soal) * 100) if total_soal > 0 else 0)

        doc_ref.set({
            "id_tugas": tg_id, "judul_tugas": tg.get("judul"), "username_siswa": username_s,
            "nama_siswa": nama_s, "kelas_siswa": kelas_s, "tipe": "pg", "jawaban": formatted_ans,
            "nilai": score, "catatan_guru": catatan, "status": "submitted", "ijin_guru": True,
            "mode_kertas": mode_kertas, "submitted_at": firestore.SERVER_TIMESTAMP, "updated_at": firestore.SERVER_TIMESTAMP
        }, merge=True)
    else:
        formatted_ans = [a if a is not None else "" for a in (answers if answers else [])]
        doc_ref.set({
            "id_tugas": tg_id, "judul_tugas": tg.get("judul"), "username_siswa": username_s,
            "nama_siswa": nama_s, "kelas_siswa": kelas_s, "tipe": "essay", "soal": soal_list,
            "jawaban": formatted_ans, "nilai": None, "catatan_guru": catatan, "status": "submitted", "ijin_guru": True,
            "mode_kertas": mode_kertas, "submitted_at": firestore.SERVER_TIMESTAMP, "updated_at": firestore.SERVER_TIMESTAMP
        }, merge=True)

    clear_pengerjaan_cache()
    return True

def delete_tugas_and_submissions(tugas_id):
    batch = db.batch()
    batch.delete(db.collection("tugas_pancasila").document(tugas_id))
    for doc in db.collection("pengerjaan_siswa").where("id_tugas", "==", tugas_id).limit(100).stream():
        batch.delete(doc.reference)
    batch.commit()
    clear_tugas_cache()
    clear_pengerjaan_cache()

def reset_pengerjaan_siswa(username_siswa, tugas_id):
    db.collection("pengerjaan_siswa").document(f"{username_siswa}_{tugas_id}").delete()
    clear_pengerjaan_cache()
    return True

# ==========================================
# 5. AI EVALUATION HELPER
# ==========================================
def koreksi_essay_dengan_ai(soal_list, jawaban_list):
    api_key = st.secrets.get("GEMINI_API_KEY") or st.secrets.get("gemini", {}).get("api_key") or st.secrets.get("firebase", {}).get("GEMINI_API_KEY")
    if not api_key: return None, "⚠️ Key 'GEMINI_API_KEY' belum dikonfigurasi di secrets Streamlit."

    try:
        genai.configure(api_key=api_key)
        total_soal = len(soal_list)
        prompt_items = []
        for i in range(total_soal):
            s = soal_list[i] if i < len(soal_list) else ""
            j = jawaban_list[i] if i < len(jawaban_list) else ""
            q_text = s.get('pertanyaan', '') if isinstance(s, dict) else str(s)
            prompt_items.append(f"Soal {i+1}: {q_text}\nJawaban Siswa: {str(j).strip() or '(Tidak dijawab)'}")

        prompt = (f"Jumlah Soal: {total_soal}\n\n" + "\n\n".join(prompt_items) +
                  "\n\nKembalikan HANYA format JSON persis: {\"nilai\": 85, \"feedback\": \"Catatan...\"}")
        system_instruction = "Anda adalah Guru Pendidikan Pancasila. Evaluasi jawaban siswa secara objektif (skala 0-100)."

        for model_name in ['gemini-2.0-flash', 'gemini-1.5-flash']:
            try:
                model = genai.GenerativeModel(model_name=model_name, system_instruction=system_instruction)
                response = model.generate_content(prompt, generation_config={"response_mime_type": "application/json"})
                if response and hasattr(response, 'text') and response.text.strip():
                    raw_text = re.sub(r'^```json\s*|\s*```$', '', response.text.strip())
                    res_json = json.loads(raw_text)
                    return int(res_json.get("nilai", 0)), str(res_json.get("feedback", "")).strip()
            except Exception: continue

        return None, "Gagal memproses respon AI."
    except Exception as e:
        return None, f"Error AI: {str(e)}"

# ==========================================
# 6. AUTHENTICATION
# ==========================================
if "user" not in st.session_state: st.session_state["user"] = None

if ensure_default_admin_created(): st.toast("💡 Akun default awal berhasil dibuat! Username: admin | Pass: admin123")

if st.session_state["user"] is None:
    st.title("🇮🇩 LMS Pendidikan Pancasila")
    st.info("💡 **Informasi**: Akun Siswa dan Guru dikelola oleh **Super Admin**.")
    with st.form("form_login"):
        username = st.text_input("Username").strip().lower()
        password = st.text_input("Password", type="password")
        if st.form_submit_button("Masuk / Login"):
            if username and password:
                all_users = {u["id"]: u for u in get_all_users_cached(limit=500)}
                if username in all_users and all_users[username].get("password") == hash_pass(password):
                    u_data = all_users[username]
                    st.session_state["user"] = {
                        "username": username, "nama": u_data.get("nama"), "role": u_data.get("role"),
                        "kelas": u_data.get("kelas", ""), "kelas_ajar": u_data.get("kelas_ajar", [])
                    }
                    st.success(f"✅ Selamat datang, {u_data.get('nama')}!")
                    st.rerun()
                else: st.error("Username atau password salah!")
            else: st.warning("Isi username dan password.")
    st.stop()

# ==========================================
# 7. SIDEBAR & NAVIGATION
# ==========================================
user_info = st.session_state["user"]
role = user_info["role"]

st.sidebar.title(f"👋 Halo, {user_info['nama']}")
caption_text = f"Role: **{role.upper()}** | @{user_info['username']}"
if role == "siswa" and user_info.get("kelas"): caption_text += f"\n\n🏫 Kelas: **{user_info['kelas']}**"
elif role == "guru" and user_info.get("kelas_ajar"):
    k_str = ", ".join(user_info['kelas_ajar']) if isinstance(user_info['kelas_ajar'], list) else user_info['kelas_ajar']
    caption_text += f"\n\n🏫 Mengajar: **{k_str}**"
st.sidebar.caption(caption_text)

if st.sidebar.button("🚪 Keluar / Logout"):
    st.session_state.clear()
    components.html("<script>sessionStorage.clear(); localStorage.clear();</script>", height=0)
    st.rerun()

st.sidebar.divider()

# ==========================================
# 8. PANEL SUPER ADMIN
# ==========================================
def render_superadmin():
    st.title("⚙️ Panel Super Admin")
    t_kelas, t_list, t_add, t_imp, t_edit, t_del = st.tabs([
        "🏫 Master Kelas", "👥 Daftar User", "➕ Buat Akun", "📥 Import/Export", "✏️ Atur Kelas", "🗑️ Hapus Akun"
    ])

    with t_kelas:
        st.subheader("🏫 Kelola Master Data Kelas")
        daftar_kelas = get_all_kelas()
        col1, col2 = st.columns(2)
        with col1:
            for k in daftar_kelas: st.markdown(f"- 🏫 **{k}**")
        with col2:
            with st.form("form_add_k", clear_on_submit=True):
                new_k = st.text_input("Nama Kelas Baru").strip()
                if st.form_submit_button("Tambah Kelas"):
                    if new_k and new_k not in daftar_kelas:
                        db.collection("config").document("master_kelas").set({"daftar": sorted(daftar_kelas + [new_k])}, merge=True)
                        clear_kelas_cache()
                        st.success(f"✅ Kelas '{new_k}' ditambahkan!")
                        st.rerun()

    with t_list:
        st.subheader("👥 Daftar Akun (Pagination)")
        role_filter = st.selectbox("Filter Peran", ["semua", "siswa", "guru", "superadmin"], format_func=lambda x: x.upper())
        curr_page, limit, offset = render_pagination_controls(count_all_users(role_filter), default_page_size=10)
        p_users = get_users_paginated(limit=limit, offset=offset, role_filter=role_filter)
        st.dataframe(pd.DataFrame([{"Username": u.get("id"), "Nama": u.get("nama"), "Role": str(u.get("role")).upper(), "Kelas": u.get("kelas") or u.get("kelas_ajar") or "-"} for u in p_users]), use_container_width=True)

    with t_add:
        st.subheader("➕ Buat Akun Satuan")
        daftar_kelas = get_all_kelas()
        new_role = st.selectbox("Role Baru", ["Siswa", "Guru", "Superadmin"])
        with st.form("form_add_user", clear_on_submit=True):
            nama = st.text_input("Nama Lengkap")
            uname = st.text_input("Username").strip().lower()
            pwd = st.text_input("Password", type="password")
            k_siswa = st.selectbox("Pilih Kelas", options=daftar_kelas) if new_role == "Siswa" and daftar_kelas else None
            k_guru = st.multiselect("Pilih Kelas Ajar", options=daftar_kelas) if new_role == "Guru" and daftar_kelas else None
            if st.form_submit_button("Buat Akun"):
                if nama and uname and pwd:
                    payload = {"nama": nama, "password": hash_pass(pwd), "password_plain": pwd, "role": new_role.lower(), "created_at": firestore.SERVER_TIMESTAMP}
                    if new_role == "Siswa": payload["kelas"] = k_siswa
                    elif new_role == "Guru": payload["kelas_ajar"] = k_guru
                    db.collection("users").document(uname).set(payload)
                    clear_users_cache()
                    st.success(f"✅ Akun '{uname}' berhasil dibuat!")
                    st.rerun()

# ==========================================
# 9. PANEL GURU (DENGAN ANALISIS & PROGRAM REMEDIAL)
# ==========================================
def render_guru():
    st.title("🇮🇩 Panel Guru Pendidikan Pancasila")
    pilihan_kelas = user_info.get("kelas_ajar") or get_all_kelas()
    if isinstance(pilihan_kelas, str): pilihan_kelas = [pilihan_kelas]

    guru_stats = get_guru_dashboard_stats(tuple(pilihan_kelas))
    st.markdown("##### ⚡ Ringkasan Statistik Real-Time")
    m_col1, m_col2, m_col3, m_col4 = st.columns(4)
    m_col1.metric("👥 Total Siswa Ajar", f"{guru_stats['total_siswa']} Siswa")
    m_col2.metric("📝 Total Tugas Dibuat", f"{guru_stats['total_tugas']} Tugas")
    m_col3.metric("📖 Total Materi", f"{guru_stats['total_materi']} Modul")
    m_col4.metric("✅ Total Jawaban Masuk", f"{guru_stats['total_submitted']} Pengumpulan")
    st.divider()

    menu = st.sidebar.radio("📌 Menu Guru", ["📖 Kelola Materi", "📝 Buat & Kelola Tugas", "📊 Rekap, Analisis & Remedial", "📜 Daftar Nilai"])

    if menu == "📖 Kelola Materi":
        st.header("📖 Kelola Materi Pembelajaran")
        materi_docs = get_all_materi_cached(limit=50)
        for m in materi_docs:
            with st.container(border=True):
                st.markdown(f"### 📘 [{m.get('bab')}] {m.get('judul')}")
                if m.get("konten"): st.write(m.get("konten"))

    elif menu == "📝 Buat & Kelola Tugas":
        st.header("📝 Buat & Kelola Tugas")
        t_list, t_kisi = st.tabs(["📋 Daftar Tugas", "🖨️ Cetak Kisi-Kisi"])
        with t_list:
        tugas_cached = get_all_tugas_cached(limit=50)
            for tg in tugas_cached:
                with st.expander(f"[{'PG' if tg.get('tipe')=='pg' else 'Essay'}] {tg.get('judul')}"):
                    st.write(f"Instruksi: {tg.get('instruksi')}")
        with t_kisi:
            st.subheader("🖨️ Cetak Kisi-Kisi Soal")
            if tugas_cached:
                tg_options_k = {t["id"]: t.get("judul", "Tanpa Judul") for t in tugas_cached}
                sel_id_k = st.selectbox("Pilih Tugas/Ujian", list(tg_options_k.keys()), format_func=lambda x: tg_options_k[x])
                selected_tg_k = next(t for t in tugas_cached if t["id"] == sel_id_k)
                if st.button("Cetak Kisi-Kisi"): 
                    kisi_kisi_data = []
                    for idx, q in enumerate(selected_tg_k.get("soal", [])):
                        q_text = q.get("pertanyaan", "") if isinstance(q, dict) else str(q)
                        opsi = q.get("opsi", []) if isinstance(q, dict) else []
                        kunci = ['A','B','C','D'][q.get("kunci", 0)] if isinstance(q, dict) and "kunci" in q and 0 <= q["kunci"] <= 3 else "-"
                        kisi_kisi_data.append({"No": idx + 1, "Pertanyaan": q_text, "Opsi": ", ".join(opsi) if opsi else "-", "Kunci": kunci})
                    df_kisi = pd.DataFrame(kisi_kisi_data)
                    st.dataframe(df_kisi, use_container_width=True)
                    st.download_button("💾 Unduh Kisi-Kisi (.csv)", df_kisi.to_csv(index=False).encode('utf-8-sig'), f"kisi_kisi_{sel_id_k}.csv", "text/csv", use_container_width=True)
            else:
                st.info("Belum ada tugas yang dibuat.")

    elif menu == "📊 Rekap, Analisis & Remedial":
        st.header("📊 Rekap, Analisis Butir Soal & Program Remedial")
        if not pilihan_kelas: st.warning("⚠️ Anda belum ditugaskan mengajar kelas manapun."); st.stop()

        col_k, col_t = st.columns(2)
        with col_k: selected_kelas = st.selectbox("🏫 Pilih Kelas Ajar", options=pilihan_kelas)

        tugas_kelas = [d for d in get_all_tugas_cached(limit=50) if is_target_sesuai_kelas(d, selected_kelas)]
        if not tugas_kelas: st.info(f"Belum ada tugas untuk Kelas **{selected_kelas}**."); st.stop()

        with col_t:
            tg_options = {t["id"]: f"[{t.get('jenis_tugas', 'Ulangan Harian')}] {t.get('judul')}" for t in tugas_kelas}
            selected_tugas_id = st.selectbox("📝 Pilih Tugas/Ulangan", list(tg_options.keys()), format_func=lambda x: tg_options[x])
            selected_tugas = next(t for t in tugas_kelas if t["id"] == selected_tugas_id)

        siswa_list = get_siswa_by_kelas_cached(selected_kelas, limit=150)
        sub_list = get_pengerjaan_by_tugas_kelas_cached(selected_tugas_id, selected_kelas, limit=150)
        sub_map = {s.get("username_siswa"): s for s in sub_list}

        # TABS UTAMA EVALUASI
        t_rekap, t_koreksi, t_analisis, t_remedial, t_program_remed, t_kontrol, t_reset = st.tabs([
            "📋 Rekap Pengerjaan", 
            "✏️ Koreksi & Penilaian", 
            "📈 Analisis Butir Soal Lengkap", 
            "🎯 Daftar Ketuntasan & Remedial (KKM 70)", 
            "🛠️ Program Remedial & Pengayaan", 
            "🔓 Kontrol Izin", 
            "🔄 Reset Pengerjaan"
        ])

        # 1. TAB REKAP
        with t_rekap:
            rekap_rows = []
            for s in siswa_list:
                un = s["username"]
                sub = sub_map.get(un, {})
                rekap_rows.append({
                    "Username": un, "Nama Siswa": s.get("nama", un),
                    "Status": "✅ Sudah Submit" if sub.get("status") == "submitted" else "❌ Belum",
                    "Nilai": sub.get("nilai") if sub.get("nilai") is not None else "-",
                    "Catatan Guru": sub.get("catatan_guru", "-")
                })
            st.dataframe(pd.DataFrame(rekap_rows), use_container_width=True)

        # 2. TAB KOREKSI
        with t_koreksi:
            submitted_docs = [s for s in sub_list if s.get("status") == "submitted"]
            if not submitted_docs: st.info("Belum ada pengumpulan.")
            else:
                for sub in submitted_docs:
                    with st.expander(f"👤 {sub.get('nama_siswa')} — Nilai: {sub.get('nilai', 'Belum')}"):
                        st.write(f"Jawaban: {sub.get('jawaban')}")
                        if selected_tugas.get("tipe") == "essay" and st.button("🤖 Koreksi AI", key=f"ai_{sub['id']}"):
                            val, fb = koreksi_essay_dengan_ai(selected_tugas.get("soal", []), sub.get("jawaban", []))
                            if val is not None:
                                db.collection("pengerjaan_siswa").document(sub['id']).update({"nilai": val, "catatan_guru": fb})
                                clear_pengerjaan_cache()
                                st.success("✅ Nilai AI tersimpan!")
                                st.rerun()

        # 3. TAB ANALISIS BUTIR SOAL LENGKAP
        with t_analisis:
            st.subheader("📈 Analisis Butir Soal Lengkap (PG)")
            submitted_pg = [s for s in sub_list if s.get("status") == "submitted" and s.get("jawaban")]
            
            if selected_tugas.get("tipe") != "pg":
                st.info("ℹ️ Analisis butir soal ini khusus untuk tugas bertipe **Pilihan Ganda (PG)**.")
            elif not submitted_pg:
                st.info("ℹ️ Belum ada siswa yang mengumpulkan jawaban PG.")
            else:
                soal_master = selected_tugas.get("soal", [])
                hasil_analisis, stats_analisis = hitung_analisis_butir_soal(soal_master, submitted_pg)

                # Summary Cards
                c_a1, c_a2, c_a3, c_a4, c_a5 = st.columns(5)
                c_a1.metric("Total Responden", f"{stats_analisis['total_responden']} Siswa")
                c_a2.metric("Kelompok Atas/Bawah", f"{stats_analisis['n_upper']} Siswa")
                c_a3.metric("✅ Soal Diterima", f"{stats_analisis['diterima']} Soal")
                c_a4.metric("⚠️ Soal Direvisi", f"{stats_analisis['direvisi']} Soal")
                c_a5.metric("❌ Soal Dibuang", f"{stats_analisis['dibuang']} Soal")
                st.divider()

                # Table Display
                df_analisis = pd.DataFrame(hasil_analisis)
                display_cols = ["No", "Pertanyaan", "Kunci", "Benar", "Tingkat Kesukaran (P)", "Kategori Kesukaran", "Daya Beda (D)", "Kategori Daya Beda", "Status Daya Beda", "Rekomendasi"]
                st.dataframe(df_analisis[display_cols], use_container_width=True)

                # Detail per Soal
                st.subheader("🔍 Detail Sebaran Jawaban & Pengecoh (Distraktor)")
                selected_no = st.selectbox("Pilih Nomor Soal untuk Detail:", list(range(1, len(soal_master) + 1)), format_func=lambda x: f"Soal No. {x}")
                item_detail = hasil_analisis[selected_no - 1]

                col_d1, col_d2 = st.columns(2)
                with col_d1:
                    st.markdown(f"**Soal #{item_detail['No']}:** {item_detail['Pertanyaan']}")
                    st.markdown(f"**Kunci Jawaban:** Opsi {item_detail['Kunci']}")
                    st.write(f"**Distraktor & Pilihan:** {item_detail['Distraktor (A, B, C, D)']}")
                with col_d2:
                    st.info(f"**Tingkat Kesukaran ($P$):** {item_detail['Tingkat Kesukaran (P)']} ({item_detail['Kategori Kesukaran']})\n\n"
                            f"**Daya Beda ($D$):** {item_detail['Daya Beda (D)']} ({item_detail['Kategori Daya Beda']})\n\n"
                            f"**Rekomendasi Akhir:** {item_detail['Rekomendasi']}")

                st.download_button(
                    "💾 Unduh Rekap Analisis Butir Soal (.csv)",
                    df_analisis.to_csv(index=False).encode('utf-8-sig'),
                    f"analisis_butir_soal_{selected_kelas}_{selected_tugas_id}.csv",
                    "text/csv",
                    use_container_width=True
                )

        # 4. TAB DAFTAR KETUNTASAN & REMEDIAL (KKM 70)
        with t_remedial:
            st.subheader("🎯 Rekap Ketuntasan Belajar & Daftar Nama Remedial")
            kkm_val = st.number_input("Tentukan Nilai KKM (Kriteria Ketuntasan Minimal)", min_value=0, max_value=100, value=70, step=5)

            total_siswa = len(siswa_list)
            siswa_tuntas = []
            siswa_remedial = []

            for s in siswa_list:
                un = s["username"]
                nm = s.get("nama", un)
                sub = sub_map.get(un, {})
                nilai = sub.get("nilai")

                if sub.get("status") == "submitted" and nilai is not None:
                    if float(nilai) >= kkm_val:
                        siswa_tuntas.append({
                            "Username": un, "Nama Siswa": nm, "Nilai": nilai,
                            "Kelebihan Nilai": int(nilai - kkm_val), "Predikat": "Sangat Baik" if nilai >= 85 else "Baik",
                            "Keterangan": "🟢 TUNTAS (Mengikuti Pengayaan)"
                        })
                    else:
                        siswa_remedial.append({
                            "Username": un, "Nama Siswa": nm, "Nilai": nilai,
                            "Kekurangan Selisih KKM": int(kkm_val - nilai), "Status": "🔴 BELUM TUNTAS (REMEDIAL)",
                            "Rekomendasi Tindakan": "Mengikuti Ujian / Tugas Remedial"
                        })
                else:
                    siswa_remedial.append({
                        "Username": un, "Nama Siswa": nm, "Nilai": 0,
                        "Kekurangan Selisih KKM": kkm_val, "Status": "⏳ BELUM SUBMIT / MENGUMPULKAN",
                        "Rekomendasi Tindakan": "Mengikuti Tes Susulan / Remedial"
                    })

            n_tuntas = len(siswa_tuntas)
            n_remed = len(siswa_remedial)
            pct_tuntas = round((n_tuntas / total_siswa * 100), 1) if total_siswa > 0 else 0

            # Metric Cards
            r1, r2, r3, r4 = st.columns(4)
            r1.metric("Total Siswa Kelas", f"{total_siswa} Siswa")
            r2.metric("Siswa Tuntas (≥ KKM)", f"{n_tuntas} Siswa ({pct_tuntas}%)")
            r3.metric("Siswa Remedial (< KKM)", f"{n_remed} Siswa ({round(100 - pct_tuntas, 1)}%)")
            r4.metric("Status Ketuntasan Klasikal", "✅ TUNTAS" if pct_tuntas >= 75 else "🔴 BELUM TUNTAS")
            st.divider()

            # Tabel Remedial
            st.markdown(f"### 🔴 Daftar Siswa Remedial (Nilai < {kkm_val} / Belum Mengumpulkan)")
            if siswa_remedial:
                df_remed = pd.DataFrame(siswa_remedial)
                st.dataframe(df_remed, use_container_width=True)
            else:
                st.success("🎉 Selamat! Seluruh siswa telah tuntas mencapai KKM.")

            # Tabel Tuntas
            st.markdown(f"### 🟢 Daftar Siswa Tuntas (Nilai ≥ {kkm_val})")
            if siswa_tuntas:
                df_tuntas = pd.DataFrame(siswa_tuntas)
                st.dataframe(df_tuntas, use_container_width=True)

            # Download Recap Button
            recap_combine = pd.concat([
                pd.DataFrame(siswa_remedial).assign(Kelompok="REMEDIAL"),
                pd.DataFrame(siswa_tuntas).assign(Kelompok="TUNTAS")
            ], ignore_index=True)
            st.download_button("💾 Unduh Rekap Ketuntasan & Remedial (.csv)", recap_combine.to_csv(index=False).encode('utf-8-sig'), f"rekap_remedial_kkm{kkm_val}_{selected_kelas}.csv", "text/csv", use_container_width=True)

        # 5. TAB PROGRAM REMEDIAL & PENGAYAAN
        with t_program_remed:
            st.subheader("🛠️ Program Execution Remedial & Pengayaan")
            kkm_prog = 70
            remed_users = [s["username"] for s in siswa_list if sub_map.get(s["username"], {}).get("nilai") is None or float(sub_map.get(s["username"], {}).get("nilai", 0)) < kkm_prog]
            pct_klasikal = round((len(siswa_list) - len(remed_users)) / len(siswa_list) * 100, 1) if siswa_list else 0

            # Rekomendasi Pelaksanaan
            st.markdown("##### 📌 Rekomendasi Strategi Pelaksanaan Remedial")
            if pct_klasikal < 50:
                st.error(f"⚠️ **Ketuntasan Klasikal Rendah ({pct_klasikal}%)**: Disarankan melakukan **Pembelajaran Ulang Klasikal** untuk seluruh kelas sebelum Tes Ulang Remedial.")
            elif pct_klasikal < 75:
                st.warning(f"⚡ **Ketuntasan Klasikal Sedang ({pct_klasikal}%)**: Disarankan memberikan **Bimbingan Kelompok / Tutor Sebaya** bagi siswa remedial.")
            else:
                st.success(f"🎉 **Ketuntasan Klasikal Tinggi ({pct_klasikal}%)**: Cukup berikan **Bimbingan Perorangan & Penugasan Remedial Mandiri**.")

            st.divider()

            # Eksekusi Reset Remedial
            st.markdown("### ⚡ Aksi Cepat Remedial")
            col_act1, col_act2 = st.columns(2)
            with col_act1:
                if st.button("🔄 Reset Pengerjaan Seluruh Siswa Remedial", type="primary", use_container_width=True):
                    for un_r in remed_users:
                        reset_pengerjaan_siswa(un_r, selected_tugas_id)
                    st.success(f"✅ Berhasil mereset status {len(remed_users)} siswa remedial agar dapat mengerjakan ulang!")
                    st.rerun()

            with col_act2:
                st.info("Siswa yang di-reset dapat langsung mengerjakan ujian kembali dari Dashboard Siswa.")

            st.divider()

            # Form Penyusunan Laporan Program Remedial
            st.markdown("### 📝 Form Rencana & Laporan Pelaksanaan Program Remedial")
            with st.form("form_laporan_remedial"):
                tgl_remed = st.date_input("Tanggal Pelaksanaan Remedial", datetime.now())
                materi_remed = st.text_input("Materi Utama / Indikator yang Di-remedialkan", value=f"Indikator pada {selected_tugas.get('judul')}")
                bentuk_kegiatan = st.selectbox("Bentuk Kegiatan Remedial", ["Pembelajaran Ulang Klasikal", "Bimbingan Kelompok", "Tutor Sebaya", "Tugas Mandiri / Penugasan Khusus"])
                target_remed = st.number_input("Target Nilai Setelah Remedial", min_value=70, max_value=100, value=75)

                if st.form_submit_button("📄 Cetak Dokumen Program Remedial"):
                    doc_report = f"""
=====================================================
LAPORAN PELAKSANAAN PROGRAM REMEDIAL & PENGAYAAN
=====================================================
Mata Pelajaran  : Pendidikan Pancasila
Kelas / Tugas   : {selected_kelas} / {selected_tugas.get('judul')}
Tanggal Remedial: {tgl_remed.strftime('%d-%m-%Y')}
Bentuk Kegiatan : {bentuk_kegiatan}
Materi Remedial : {materi_remed}
Target Nilai    : {target_remed}

DAFTAR SISWA REMEDIAL ({len(remed_users)} Siswa):
-----------------------------------------------------
"""
                    for idx_r, un_r in enumerate(remed_users, 1):
                        nm_r = sub_map.get(un_r, {}).get("nama_siswa", un_r)
                        n_awal = sub_map.get(un_r, {}).get("nilai", 0)
                        doc_report += f"{idx_r}. {nm_r} (@{un_r}) | Nilai Awal: {n_awal} | Target: {target_remed}\n"

                    st.download_button("💾 Unduh Dokumen Laporan Program Remedial (.txt)", doc_report.encode('utf-8'), f"laporan_program_remedial_{selected_kelas}.txt", "text/plain")

        # 6. TAB KONTROL IZIN
        with t_kontrol:
            st.subheader("🔓 Kontrol Izin & Buka Kunci Siswa")
            locked_students = [s for s in siswa_list if sub_map.get(s["username"], {}).get("status") == "in_progress" and not sub_map.get(s["username"], {}).get("ijin_guru", True)]
            if not locked_students: st.info("ℹ️ Tidak ada siswa yang terkunci.")
            else:
                for ls in locked_students:
                    if st.button(f"🔓 Beri Izin {ls.get('nama')}", key=f"btn_grant_{ls['username']}"):
                        db.collection("pengerjaan_siswa").document(f"{ls['username']}_{selected_tugas_id}").set({"ijin_guru": True}, merge=True)
                        clear_pengerjaan_cache()
                        st.rerun()

        # 7. TAB RESET PENGERJAAN
        with t_reset:
            st.subheader("🔄 Reset Pengerjaan Siswa")
            siswa_pengerjaan = [s for s in siswa_list if s["username"] in sub_map]
            if siswa_pengerjaan:
                sel_indiv = st.selectbox("Pilih Siswa Reset", [s["username"] for s in siswa_pengerjaan])
                if st.button("🗑️ Reset Siswa Ini", type="primary"):
                    reset_pengerjaan_siswa(sel_indiv, selected_tugas_id)
                    st.success("✅ Berhasil di-reset!")
                    st.rerun()

    elif menu == "📜 Daftar Nilai":
        st.header("📜 Transkrip & Daftar Nilai Siswa")
        selected_kelas = st.selectbox("🏫 Pilih Kelas", options=pilihan_kelas)
        tugas_kelas = [d for d in get_all_tugas_cached(limit=50) if is_target_sesuai_kelas(d, selected_kelas)]
        siswa_list = get_siswa_by_kelas_cached(selected_kelas, limit=150)
        sub_list = get_all_pengerjaan_by_kelas_cached(selected_kelas, limit=300)
        sub_map = {(s.get("username_siswa"), s.get("id_tugas")): s for s in sub_list}

        table_rows = []
        for s in siswa_list:
            un = s["username"]
            row = {"Username": un, "Nama Siswa": s.get("nama", un)}
            scores = []
            for tg in tugas_kelas:
                sub = sub_map.get((un, tg["id"]), {})
                val = sub.get("nilai")
                if sub.get("status") == "submitted" and val is not None:
                    row[tg.get("judul")] = val
                    scores.append(float(val))
                else: row[tg.get("judul")] = "-"
            row["Rata-Rata"] = round(sum(scores)/len(scores), 2) if scores else "-"
            table_rows.append(row)

        df_nilai = pd.DataFrame(table_rows)
        st.dataframe(df_nilai, use_container_width=True)
        st.download_button("💾 Unduh Rekap Nilai (.csv)", df_nilai.to_csv(index=False).encode('utf-8-sig'), f"rekap_nilai_{selected_kelas}.csv", "text/csv")

# ==========================================
# 10. PANEL SISWA (CBT INTERFACE)
# ==========================================
def render_siswa():
    kelas_s = user_info.get("kelas", "-")
    nama_s = user_info.get("nama", "Siswa")
    username_s = user_info.get("username", "")

    my_subs = get_user_pengerjaan_cached(username_s)
    active_quiz_id = st.session_state.get("active_quiz_id")

    if active_quiz_id:
        tg = next((t for t in get_all_tugas_cached(limit=100) if t["id"] == active_quiz_id), None)
        if not tg: st.session_state["active_quiz_id"] = None; st.rerun()

        st.markdown(f"## 📝 {tg.get('judul')}")
        if st.button("⬅️ Kembali ke Dashboard"):
            st.session_state["active_quiz_id"] = None
            st.rerun()

        soal_list = tg.get("soal", [])
        with st.form(key=f"cbt_form_{tg['id']}"):
            jawaban_user = []
            for idx, sq in enumerate(soal_list):
                st.markdown(f"#### **{idx + 1}. {sq.get('pertanyaan', '')}**")
                if tg.get("tipe") == "pg":
                    opsi = sq.get("opsi", ["A", "B", "C", "D"])
                    ans = st.radio(f"Pilih Jawaban #{idx+1}:", options=list(range(len(opsi))), format_func=lambda x: f"{['A','B','C','D'][x]}. {opsi[x]}", key=f"ans_{idx}", index=None)
                    jawaban_user.append(ans if ans is not None else -1)
                else:
                    ans = st.text_area(f"Jawaban Essay #{idx+1}:", key=f"essay_{idx}")
                    jawaban_user.append(ans)
                st.divider()

            if st.form_submit_button("🚀 Kirim Jawaban", type="primary", use_container_width=True):
                submit_jawaban_siswa(tg, username_s, nama_s, kelas_s, answers=jawaban_user)
                st.session_state["active_quiz_id"] = None
                clear_pengerjaan_cache()
                st.success("✅ Jawaban berhasil dikirim!")
                st.rerun()
        st.stop()

    st.markdown(f"""
        <div class="student-header">
            <h2>🇮🇩 Dashboard Siswa Pendidikan Pancasila</h2>
            <p>Selamat Datang, <b>{nama_s}</b> | Kelas: <b>{kelas_s}</b></p>
        </div>
    """, unsafe_allow_html=True)

    t_materi_s, t_tugas_s = st.tabs(["📖 Materi Pembelajaran", "📝 Tugas & Ujian Saya"])

    with t_materi_s:
        for m in [m for m in get_all_materi_cached(limit=50) if is_target_sesuai_kelas(m, kelas_s)]:
            with st.container(border=True):
                st.markdown(f"### 📘 [{m.get('bab')}] {m.get('judul')}")
                if m.get("konten"): st.write(m.get("konten"))

    with t_tugas_s:
        tugas_siswa = [t for t in get_all_tugas_cached(limit=50) if is_target_sesuai_kelas(t, kelas_s) and t.get("status", "terbit") == "terbit"]
        for tg in tugas_siswa:
            sub = my_subs.get(tg["id"], {})
            status_sub = sub.get("status", "belum")
            with st.container(border=True):
                col_i, col_b = st.columns([3, 1])
                with col_i:
                    st.markdown(f"### 📝 {tg.get('judul')}")
                    if status_sub == "submitted":
                        st.success(f"✅ Sudah Dikumpulkan | Nilai: {sub.get('nilai', 'Belum Dinilai')}")
                with col_b:
                    if status_sub != "submitted":
                        if st.button("Mulai Ujian 🚀", key=f"start_{tg['id']}", use_container_width=True):
                            st.session_state["active_quiz_id"] = tg["id"]
                            st.rerun()

# ==========================================
# 11. MAIN ROUTER
# ==========================================
if role == "superadmin": render_superadmin()
elif role == "guru": render_guru()
elif role == "siswa": render_siswa()
else: st.error("Role tidak dikenal.")
