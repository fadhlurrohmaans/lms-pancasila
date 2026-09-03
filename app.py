import os
import json
import io
import re
import random
import string
import hashlib
import time
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
    
    div[data-testid="stTextInput"]:has(input[aria-label="Draft Bridge Input"]) {
        display: none !important;
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

# --- FIRESTORE AGGREGATION & CACHING HELPERS ---
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
    total_submitted = 0
    kelas_list = list(pilihan_kelas_tuple)

    if kelas_list:
        for i in range(0, len(kelas_list), 30):
            chunk = kelas_list[i:i+30]
            try:
                q_siswa = db.collection("users").where("role", "==", "siswa").where("kelas", "in", chunk)
                total_siswa += q_siswa.count().get()[0][0].value
            except Exception:
                pass

            try:
                q_sub = db.collection("pengerjaan_siswa").where("kelas_siswa", "in", chunk).where("status", "==", "submitted")
                total_submitted += q_sub.count().get()[0][0].value
            except Exception:
                pass

    return {
        "total_siswa": total_siswa,
        "total_tugas": total_tugas,
        "total_materi": total_materi,
        "total_submitted": total_submitted
    }

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
def get_siswa_by_kelas_cached(kelas, limit=100, offset=0):
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

@st.cache_data(ttl=300)
def get_materi_by_kelas_server_side(kelas_siswa, limit=100):
    docs = db.collection("materi_pancasila").where("target_kelas", "array_contains", kelas_siswa).limit(limit).stream()
    return [{"id": d.id, **d.to_dict()} for d in docs]

@st.cache_data(ttl=300)
def get_tugas_by_kelas_server_side(kelas_siswa, limit=100):
    docs = db.collection("tugas_pancasila").where("target_kelas", "array_contains", kelas_siswa).limit(limit).stream()
    return [{"id": d.id, **d.to_dict()} for d in docs]

@st.cache_data(ttl=30)
def get_user_pengerjaan_cached(username, limit=50):
    docs = db.collection("pengerjaan_siswa").where("username_siswa", "==", username).limit(limit).stream()
    return {d.to_dict().get("id_tugas"): {"id": d.id, **d.to_dict()} for d in docs}

@st.cache_data(ttl=30)
def get_pengerjaan_by_tugas_kelas_cached(tugas_id, kelas, limit=150, offset=0):
    docs = db.collection("pengerjaan_siswa").where("id_tugas", "==", tugas_id).where("kelas_siswa", "==", kelas).limit(limit).offset(offset).stream()
    return [{"id": d.id, **d.to_dict()} for d in docs]

@st.cache_data(ttl=30)
def get_pengerjaan_by_kelas_list_cached(pilihan_kelas_tuple, limit=500):
    if not pilihan_kelas_tuple:
        return []
    kelas_list = list(pilihan_kelas_tuple)
    results = []
    for i in range(0, len(kelas_list), 30):
        chunk = kelas_list[i:i+30]
        docs = db.collection("pengerjaan_siswa").where("kelas_siswa", "in", chunk).limit(limit).stream()
        results.extend([d.to_dict() for d in docs])
    return results

@st.cache_data(ttl=30)
def get_all_pengerjaan_by_kelas_cached(kelas, limit=300):
    docs = db.collection("pengerjaan_siswa").where("kelas_siswa", "==", kelas).limit(limit).stream()
    return [d.to_dict() for d in docs]

# CACHE CLEAR HELPERS
def clear_kelas_cache(): 
    get_all_kelas.clear()
    get_guru_dashboard_stats.clear()

def clear_tugas_cache(): 
    get_all_tugas_cached.clear()
    get_tugas_by_kelas_server_side.clear()
    get_guru_dashboard_stats.clear()

def clear_materi_cache(): 
    get_all_materi_cached.clear()
    get_materi_by_kelas_server_side.clear()
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
    get_pengerjaan_by_kelas_list_cached.clear()
    count_submitted_by_tugas_kelas.clear()
    get_guru_dashboard_stats.clear()

# ==========================================
# 3. FIRESTORE DATA BUNDLES IMPLEMENTATION
# ==========================================
@st.cache_data(ttl=3600)
def generate_firestore_data_bundle():
    if FirestoreBundle is None:
        return None, "Modul `google.cloud.firestore_bundle` tidak tersedia di lingkungan ini."

    try:
        bundle = FirestoreBundle("lms_master_data_bundle")
        kelas_ref = db.collection("config").document("master_kelas")
        kelas_snap = kelas_ref.get()
        if kelas_snap.exists:
            bundle.add_document(kelas_snap)

        materi_query = db.collection("materi_pancasila").limit(50)._query()
        bundle.add_named_query("bundle_all_materi", materi_query)

        tugas_query = db.collection("tugas_pancasila").limit(50)._query()
        bundle.add_named_query("bundle_all_tugas", tugas_query)

        serialized_bundle = bundle.build()
        return serialized_bundle, None
    except Exception as e:
        return None, f"Gagal membuat Data Bundle: {str(e)}"

# ==========================================
# 4. UTILITY & RANDOMIZATION HELPERS
# ==========================================
def randomize_soal(soal_master, tipe="pg"):
    """
    Mengacak urutan nomor soal dan pilihan jawaban (opsi) untuk setiap soal PG.
    Memperbarui kunci jawaban secara otomatis agar tetap menunjuk ke opsi yang benar.
    """
    if not soal_master:
        return []
    
    shuffled_soal = []
    for idx, sq in enumerate(soal_master):
        if not isinstance(sq, dict):
            shuffled_soal.append(sq)
            continue
            
        sq_copy = dict(sq)
        sq_copy["orig_idx"] = idx  # Menyimpan indeks soal asli untuk keperluan rekap/analisis Guru
        
        if tipe == "pg" and "opsi" in sq_copy and isinstance(sq_copy["opsi"], list):
            orig_opsi = list(sq_copy["opsi"])
            orig_kunci = sq_copy.get("kunci", 0)
            
            # Ambil teks opsi jawaban yang benar
            if isinstance(orig_kunci, int) and 0 <= orig_kunci < len(orig_opsi):
                kunci_text = orig_opsi[orig_kunci]
            else:
                kunci_text = orig_opsi[0] if orig_opsi else ""
            
            # Acak urutan opsi jawaban
            new_opsi = list(orig_opsi)
            random.shuffle(new_opsi)
            
            # Cari indeks kunci jawaban yang baru pada opsi teracak
            try:
                new_kunci = new_opsi.index(kunci_text)
            except ValueError:
                new_kunci = 0
                
            sq_copy["opsi"] = new_opsi
            sq_copy["kunci"] = new_kunci
            
        shuffled_soal.append(sq_copy)
        
    # Acak urutan nomor soal
    random.shuffle(shuffled_soal)
    return shuffled_soal

def get_student_ans_for_master_q(sub, master_idx, master_q):
    """
    Memetakan kembali jawaban teracak siswa ke soal master untuk analisis butir soal Guru.
    Mengembalikan tuple: (is_correct: 1/0, master_option_index: 0..3/None)
    """
    sub_soal = sub.get("soal", [])
    ans_list = sub.get("jawaban", [])
    
    if not sub_soal:
        user_a = ans_list[master_idx] if master_idx < len(ans_list) else None
        is_corr = 1 if (isinstance(user_a, int) and user_a == master_q.get("kunci", 0)) else 0
        return is_corr, user_a
        
    master_text = master_q.get("pertanyaan", "") if isinstance(master_q, dict) else str(master_q)
    master_opsi = master_q.get("opsi", []) if isinstance(master_q, dict) else []
    
    for sq_i, sq in enumerate(sub_soal):
        if not isinstance(sq, dict):
            continue
        if sq.get("orig_idx") == master_idx or sq.get("pertanyaan") == master_text:
            user_a = ans_list[sq_i] if sq_i < len(ans_list) else None
            if user_a is None or not isinstance(user_a, int) or user_a < 0:
                return 0, None
            
            is_corr = 1 if user_a == sq.get("kunci") else 0
            
            sq_opsi = sq.get("opsi", [])
            if 0 <= user_a < len(sq_opsi):
                chosen_text = sq_opsi[user_a]
                if chosen_text in master_opsi:
                    master_opt_idx = master_opsi.index(chosen_text)
                    return is_corr, master_opt_idx
            return is_corr, user_a
            
    return 0, None

def render_pagination_controls(total_items, default_page_size=10, key_prefix="pg"):
    if total_items <= 0:
        return 1, default_page_size, 0

    col_p1, col_p2, col_p3 = st.columns([2, 2, 4])
    
    with col_p1:
        page_size = st.selectbox(
            "Tampilkan per Halaman", 
            options=[5, 10, 20, 50, 100], 
            index=[5, 10, 20, 50, 100].index(default_page_size) if default_page_size in [5, 10, 20, 50, 100] else 1,
            key=f"{key_prefix}_size"
        )
    
    total_pages = max(1, (total_items + page_size - 1) // page_size)

    with col_p2:
        curr_page = st.number_input(
            f"Halaman (1 - {total_pages})", 
            min_value=1, 
            max_value=total_pages, 
            value=1, 
            step=1, 
            key=f"{key_prefix}_num"
        )

    offset = (curr_page - 1) * page_size

    with col_p3:
        start_item = offset + 1
        end_item = min(offset + page_size, total_items)
        st.markdown(f"<p style='padding-top:25px; color:#666;'>Showing <b>{start_item}-{end_item}</b> of <b>{total_items}</b> items</p>", unsafe_allow_html=True)

    return curr_page, page_size, offset

def safe_read_uploaded_file(uploaded_file):
    if uploaded_file.name.endswith('.csv'):
        for enc in ['utf-8', 'utf-8-sig', 'cp1252', 'latin1']:
            try:
                uploaded_file.seek(0)
                return pd.read_csv(uploaded_file, encoding=enc)
            except (UnicodeDecodeError, UnicodeError):
                continue
        uploaded_file.seek(0)
        return pd.read_csv(uploaded_file, encoding='utf-8', errors='replace')
    return pd.read_excel(uploaded_file)

def hash_pass(password):
    return hashlib.sha256(password.encode()).hexdigest()

def generate_username(nama, existing_usernames=None):
    if existing_usernames is None:
        existing_usernames = {u["id"] for u in get_all_users_cached()}
    first_name = nama.strip().split()[0] if nama.strip() else "siswa"
    base_username = re.sub(r'[^a-z0-9]', '', first_name.lower())[:5] or "siswa"
    
    username, counter = base_username, 1
    while username in existing_usernames:
        username = f"{base_username}{counter}"
        counter += 1
    return username
    
def generate_password(length=6):
    return ''.join(random.choices(string.ascii_lowercase + string.digits, k=length))

def submit_jawaban_siswa(tg, username_s, nama_s, kelas_s, answers, is_forced=False, is_violation=False):
    tg_id = tg["id"]
    soal_list = tg.get("soal", [])
    total_soal = len(soal_list)
    
    if is_violation:
        catatan = "⚠️ Submit Otomatis (Mencapai Limit Maksimal Pelanggaran)"
    elif is_forced:
        catatan = "Di-submit Paksa oleh Guru"
    else:
        catatan = "Penilaian Otomatis Sistem"
    
    doc_ref = db.collection("pengerjaan_siswa").document(f"{username_s}_{tg_id}")

    if tg.get("tipe") == "pg":
        correct_count = 0
        formatted_ans = []
        for idx_q, sq in enumerate(soal_list):
            user_a = answers[idx_q] if answers and idx_q < len(answers) else None
            formatted_ans.append(user_a if user_a is not None else -1)
            if user_a is not None and user_a == sq.get("kunci"):
                correct_count += 1
        score = round((correct_count / total_soal) * 100) if total_soal > 0 else 0

        doc_ref.set({
            "id_tugas": tg_id, "judul_tugas": tg.get("judul"), "username_siswa": username_s,
            "nama_siswa": nama_s, "kelas_siswa": kelas_s, "tipe": "pg", "soal": soal_list, "jawaban": formatted_ans,
            "nilai": score, "catatan_guru": catatan, "status": "submitted", "ijin_guru": True,
            "submitted_at": firestore.SERVER_TIMESTAMP, "updated_at": firestore.SERVER_TIMESTAMP
        }, merge=True)
    else:
        formatted_ans = [a if a is not None else "" for a in (answers if answers else [])]
        doc_ref.set({
            "id_tugas": tg_id, "judul_tugas": tg.get("judul"), "username_siswa": username_s,
            "nama_siswa": nama_s, "kelas_siswa": kelas_s, "tipe": "essay", "soal": soal_list,
            "jawaban": formatted_ans, "nilai": None, "catatan_guru": catatan, "status": "submitted", "ijin_guru": True,
            "submitted_at": firestore.SERVER_TIMESTAMP, "updated_at": firestore.SERVER_TIMESTAMP
        }, merge=True)

    clear_pengerjaan_cache()
    return True

def submit_jawaban_bulk(tg, list_siswa_unsub, kelas_s):
    tg_id = tg["id"]
    soal_list = tg.get("soal", [])
    total_soal = len(soal_list)
    
    for i in range(0, len(list_siswa_unsub), 500):
        batch = db.batch()
        chunk = list_siswa_unsub[i:i+500]
        for s_unsub in chunk:
            username_s = s_unsub["username"]
            nama_s = s_unsub.get("nama", username_s)
            doc_ref = db.collection("pengerjaan_siswa").document(f"{username_s}_{tg_id}")

            if tg.get("tipe") == "pg":
                formatted_ans = [-1] * total_soal
                batch.set(doc_ref, {
                    "id_tugas": tg_id, "judul_tugas": tg.get("judul"), "username_siswa": username_s,
                    "nama_siswa": nama_s, "kelas_siswa": kelas_s, "tipe": "pg", "soal": soal_list, "jawaban": formatted_ans,
                    "nilai": 0, "catatan_guru": "Di-submit Paksa oleh Guru", "status": "submitted", "ijin_guru": True,
                    "submitted_at": firestore.SERVER_TIMESTAMP, "updated_at": firestore.SERVER_TIMESTAMP
                }, merge=True)
            else:
                formatted_ans = [""] * total_soal
                batch.set(doc_ref, {
                    "id_tugas": tg_id, "judul_tugas": tg.get("judul"), "username_siswa": username_s,
                    "nama_siswa": nama_s, "kelas_siswa": kelas_s, "tipe": "essay", "soal": soal_list,
                    "jawaban": formatted_ans, "nilai": None, "catatan_guru": "Di-submit Paksa oleh Guru", "status": "submitted", "ijin_guru": True,
                    "submitted_at": firestore.SERVER_TIMESTAMP, "updated_at": firestore.SERVER_TIMESTAMP
                }, merge=True)
        batch.commit()
    
    clear_pengerjaan_cache()
    return True

def delete_tugas_and_submissions(tugas_id):
    p_docs = list(db.collection("pengerjaan_siswa").where("id_tugas", "==", tugas_id).stream())
    for i in range(0, len(p_docs), 500):
        batch = db.batch()
        chunk = p_docs[i:i+500]
        for doc in chunk:
            batch.delete(doc.reference)
        batch.commit()
    
    db.collection("tugas_pancasila").document(tugas_id).delete()
    clear_tugas_cache()
    clear_pengerjaan_cache()

def reset_pengerjaan_siswa_bulk(usernames, tugas_id):
    if isinstance(usernames, str):
        usernames = [usernames]
    
    for i in range(0, len(usernames), 500):
        batch = db.batch()
        chunk = usernames[i:i+500]
        for un in chunk:
            doc_ref = db.collection("pengerjaan_siswa").document(f"{un}_{tugas_id}")
            batch.delete(doc_ref)
        batch.commit()
        
    clear_pengerjaan_cache()
    return True

def reset_pengerjaan_siswa(username_siswa, tugas_id):
    return reset_pengerjaan_siswa_bulk([username_siswa], tugas_id)

# ==========================================
# 5. AI EVALUATION HELPER
# ==========================================
def koreksi_essay_dengan_ai(soal_list, jawaban_list):
    api_key = st.secrets.get("GEMINI_API_KEY") or st.secrets.get("gemini", {}).get("api_key") or st.secrets.get("firebase", {}).get("GEMINI_API_KEY")
    if not api_key:
        return None, "⚠️ Key 'GEMINI_API_KEY' belum dikonfigurasi di secrets Streamlit."

    try:
        genai.configure(api_key=api_key)
        total_soal = len(soal_list)
        prompt_items = []
        for i in range(total_soal):
            s = soal_list[i] if i < len(soal_list) else ""
            j = jawaban_list[i] if i < len(jawaban_list) else ""
            q_text = s.get('pertanyaan', '') if isinstance(s, dict) else str(s)
            j_text = str(j).strip() if j and str(j).strip() else '(Siswa tidak menjawab)'
            prompt_items.append(f"Soal {i+1}: {q_text}\nJawaban Siswa: {j_text}")

        prompt = (
            f"Jumlah Soal: {total_soal}\n\n"
            + "\n\n".join(prompt_items)
            + "\n\nKembalikan HANYA format JSON persis seperti berikut tanpa teks ekstra:\n"
            '{"nilai": 85, "feedback": "Catatan koreksi Anda..."}'
        )

        system_instruction = (
            "Anda adalah Guru Pendidikan Pancasila. Evaluasi jawaban siswa secara objektif (skala 0-100).\n"
            "Hitung nilai rata-rata integer (0-100) dan berikan feedback per nomor yang ramah serta edukatif."
        )

        candidate_models = ['gemini-2.0-flash', 'gemini-1.5-flash', 'gemini-1.5-flash-latest']
        response = None
        last_error = None

        for model_name in candidate_models:
            try:
                model = genai.GenerativeModel(
                    model_name=model_name,
                    system_instruction=system_instruction
                )
                response = model.generate_content(
                    prompt,
                    generation_config={"response_mime_type": "application/json"}
                )
                if response and hasattr(response, 'text') and response.text.strip():
                    break
            except Exception as err:
                last_error = err
                continue

        if not response or not hasattr(response, 'text') or not response.text.strip():
            return None, f"AI tidak merespon. Error: {str(last_error)}"

        raw_text = response.text.strip()
        raw_text = re.sub(r'^```json\s*', '', raw_text)
        raw_text = re.sub(r'\s*```$', '', raw_text)

        result_json = json.loads(raw_text)
        nilai = int(result_json.get("nilai", 0))
        feedback = str(result_json.get("feedback", "")).strip() or "Terima kasih telah mengerjakan!"

        return nilai, feedback

    except Exception as e:
        return None, f"Gagal mengeksekusi AI: {str(e)}"

# ==========================================
# 6. AUTHENTICATION
# ==========================================
if "user" not in st.session_state:
    st.session_state["user"] = None

if ensure_default_admin_created():
    st.toast("💡 Akun default awal berhasil dibuat! Username: admin | Pass: admin123")

if st.session_state["user"] is None:
    st.title("🇮🇩 LMS Pendidikan Pancasila")
    st.info("💡 **Informasi**: Akun Siswa dan Guru dikelola oleh **Super Admin**.")
    
    with st.form("form_login"):
        username = st.text_input("Username").strip().lower()
        password = st.text_input("Password", type="password")
        if st.form_submit_button("Masuk / Login"):
            if username and password:
                all_users = {u["id"]: u for u in get_all_users_cached(limit=500)}
                if username in all_users:
                    user_data = all_users[username]
                    if user_data.get("password") == hash_pass(password):
                        st.session_state["user"] = {
                            "username": username,
                            "nama": user_data.get("nama"),
                            "role": user_data.get("role"),
                            "kelas": user_data.get("kelas", ""),
                            "kelas_ajar": user_data.get("kelas_ajar", [])
                        }
                        st.success(f"✅ Berhasil login! Selamat datang, {user_data.get('nama')}!")
                        st.rerun()
                    else: st.error("Password salah!")
                else: st.error("Username tidak terdaftar!")
            else: st.warning("Isi username dan password.")
    st.stop()

# ==========================================
# 7. SIDEBAR
# ==========================================
user_info = st.session_state["user"]
role = user_info["role"]

st.sidebar.title(f"👋 Halo, {user_info['nama']}")
caption_text = f"Role: **{role.upper()}** | @{user_info['username']}"
if role == "siswa" and user_info.get("kelas"):
    caption_text += f"\n\n🏫 Kelas: **{user_info['kelas']}**"
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
    t_kelas, t_list, t_add, t_imp, t_edit, t_del, t_bundle = st.tabs([
        "🏫 Master Kelas", "👥 Daftar User", "➕ Buat Akun", "📥 Import/Export", "✏️ Atur Kelas", "🗑️ Hapus Akun", "📦 Firestore Data Bundles"
    ])

    with t_kelas:
        st.subheader("🏫 Kelola Master Data Kelas")
        col1, col2 = st.columns(2)
        daftar_kelas = get_all_kelas()
        
        with col1:
            st.write("📋 **Kelas Terdaftar:**")
            for k in daftar_kelas: st.markdown(f"- 🏫 **{k}**")
        
        with col2:
            with st.form("form_add_k", clear_on_submit=True):
                new_k = st.text_input("Nama Kelas Baru").strip()
                if st.form_submit_button("Tambah Kelas"):
                    if new_k:
                        if new_k not in daftar_kelas:
                            updated_k = sorted(daftar_kelas + [new_k])
                            db.collection("config").document("master_kelas").set({"daftar": updated_k}, merge=True)
                            clear_kelas_cache()
                            st.success(f"✅ Kelas '{new_k}' ditambahkan!")
                            st.rerun()
                        else:
                            st.warning("Kelas sudah ada!")
            
            if daftar_kelas:
                st.divider()
                del_k = st.selectbox("Pilih Kelas Dihapus", daftar_kelas)
                if st.button("Hapus Kelas", type="primary"):
                    updated_k = [k for k in daftar_kelas if k != del_k]
                    db.collection("config").document("master_kelas").set({"daftar": updated_k}, merge=True)
                    clear_kelas_cache()
                    st.success(f"✅ Kelas '{del_k}' dihapus.")
                    st.rerun()

    with t_list:
        st.subheader("👥 Daftar Akun (Dengan Pagination & Limit Query)")
        role_filter = st.selectbox("Filter Peran User", ["semua", "siswa", "guru", "superadmin"], format_func=lambda x: x.upper(), key="admin_role_filter")
        
        total_users = count_all_users(role_filter)
        curr_page, limit, offset = render_pagination_controls(total_users, default_page_size=10, key_prefix="users_pg")
        
        paginated_users_list = get_users_paginated(limit=limit, offset=offset, role_filter=role_filter)
        users_table = []
        for u in paginated_users_list:
            role_u = str(u.get("role", "")).lower()
            kelas_disp = u.get("kelas", "-") if role_u == "siswa" else (", ".join(u.get("kelas_ajar", [])) if isinstance(u.get("kelas_ajar"), list) else "-")
            users_table.append({
                "Username": u.get("id"),
                "Nama Lengkap": u.get("nama"),
                "Role": role_u.upper(),
                "Kelas / Kelas Ajar": kelas_disp,
                "Password Plain": u.get("password_plain", "*****")
            })
        if users_table:
            st.dataframe(pd.DataFrame(users_table), use_container_width=True)

    with t_add:
        st.subheader("➕ Buat Akun Baru")
        role_choice = st.selectbox("Peran (Role)", ["siswa", "guru", "superadmin"])
        with st.form("form_create_user"):
            f_nama = st.text_input("Nama Lengkap")
            f_user = st.text_input("Username (Opsional - Otomatis jika kosong)").strip().lower()
            f_pass = st.text_input("Password (Opsional - Otomatis jika kosong)").strip()
            
            daftar_kelas = get_all_kelas()
            f_kelas = None
            f_kelas_ajar = []
            if role_choice == "siswa":
                f_kelas = st.selectbox("Pilih Kelas", daftar_kelas)
            elif role_choice == "guru":
                f_kelas_ajar = st.multiselect("Pilih Kelas Ajar", daftar_kelas)

            if st.form_submit_button("Buat Akun"):
                if f_nama:
                    gen_u = f_user or generate_username(f_nama)
                    gen_p = f_pass or generate_password()
                    
                    user_ref = db.collection("users").document(gen_u)
                    if user_ref.get().exists:
                        st.error(f"Username '{gen_u}' sudah digunakan!")
                    else:
                        payload = {
                            "nama": f_nama,
                            "role": role_choice,
                            "password": hash_pass(gen_p),
                            "password_plain": gen_p,
                            "created_at": firestore.SERVER_TIMESTAMP
                        }
                        if role_choice == "siswa": payload["kelas"] = f_kelas
                        elif role_choice == "guru": payload["kelas_ajar"] = f_kelas_ajar
                        
                        user_ref.set(payload)
                        clear_users_cache()
                        st.success(f"✅ Akun berhasil dibuat! Username: **{gen_u}** | Pass: **{gen_p}**")
                        st.rerun()

    with t_imp:
        st.subheader("📥 Import User dari File (CSV / Excel)")
        up_file = st.file_uploader("Upload File CSV atau Excel", type=['csv', 'xlsx', 'xls'])
        if up_file:
            try:
                df = safe_read_uploaded_file(up_file)
                st.write("📄 Preview Data:", df.head())
                if st.button("Import Data Sekarang", type="primary"):
                    batch = db.batch()
                    count = 0
                    for idx, row in df.iterrows():
                        nama = str(row.get("nama", "")).strip()
                        role_row = str(row.get("role", "siswa")).strip().lower()
                        if nama:
                            uname = str(row.get("username", "")).strip().lower() or generate_username(nama)
                            pwd = str(row.get("password", "")).strip() or generate_password()
                            doc_ref = db.collection("users").document(uname)
                            
                            payload = {
                                "nama": nama,
                                "role": role_row,
                                "password": hash_pass(pwd),
                                "password_plain": pwd,
                                "created_at": firestore.SERVER_TIMESTAMP
                            }
                            if role_row == "siswa": payload["kelas"] = str(row.get("kelas", "")).strip()
                            elif role_row == "guru": payload["kelas_ajar"] = [k.strip() for k in str(row.get("kelas_ajar", "")).split(",") if k.strip()]
                            
                            batch.set(doc_ref, payload, merge=True)
                            count += 1
                    batch.commit()
                    clear_users_cache()
                    st.success(f"🎉 Berhasil mengimpor {count} akun!")
                    st.rerun()
            except Exception as e:
                st.error(f"Gagal memproses file: {e}")

    with t_edit:
        st.subheader("✏️ Kelola Kelas & Kelas Ajar User")
        all_users = get_all_users_cached()
        u_options = {f"{u['id']} - {u.get('nama')} ({u.get('role').upper()})": u['id'] for u in all_users if u.get('role') in ['siswa', 'guru']}
        if u_options:
            selected_u_label = st.selectbox("Pilih User", list(u_options.keys()))
            target_uid = u_options[selected_u_label]
            u_data = next((u for u in all_users if u['id'] == target_uid), None)
            
            if u_data:
                daftar_kelas = get_all_kelas()
                with st.form("form_edit_user_kelas"):
                    st.write(f"Editing: **{u_data.get('nama')}** (@{u_data['id']})")
                    if u_data.get("role") == "siswa":
                        cur_k = u_data.get("kelas", "")
                        idx_k = daftar_kelas.index(cur_k) if cur_k in daftar_kelas else 0
                        new_k = st.selectbox("Kelas Siswa", daftar_kelas, index=idx_k)
                        if st.form_submit_button("💾 Simpan Perubahan Kelas"):
                            db.collection("users").document(target_uid).update({"kelas": new_k})
                            clear_users_cache()
                            st.success("✅ Kelas siswa berhasil diperbarui!")
                            st.rerun()
                    elif u_data.get("role") == "guru":
                        cur_ka = u_data.get("kelas_ajar", [])
                        if not isinstance(cur_ka, list): cur_ka = [cur_ka]
                        new_ka = st.multiselect("Kelas Ajar Guru", daftar_kelas, default=[k for k in cur_ka if k in daftar_kelas])
                        if st.form_submit_button("💾 Simpan Perubahan Kelas Ajar Guru", type="primary"):
                            db.collection("users").document(target_uid).update({"kelas_ajar": new_ka})
                            clear_users_cache()
                            st.success(f"✅ Kelas ajar untuk guru '{u_data.get('nama')}' berhasil diperbarui!")
                            st.rerun()

    with t_del:
        st.subheader("🗑️ Hapus Akun User")
        all_users = get_all_users_cached()
        u_del_options = {f"{u['id']} - {u.get('nama')} ({u.get('role').upper()})": u['id'] for u in all_users if u['id'] != "admin"}
        if u_del_options:
            sel_del_label = st.selectbox("Pilih Akun yang Akan Dihapus", list(u_del_options.keys()))
            target_del_id = u_del_options[sel_del_label]
            if st.button("🚨 Hapus Akun Sekarang", type="primary"):
                db.collection("users").document(target_del_id).delete()
                clear_users_cache()
                st.success(f"✅ Akun '@{target_del_id}' telah dihapus.")
                st.rerun()

    with t_bundle:
        st.subheader("📦 Firestore Data Bundles")
        st.write("Generate bundle data Firestore untuk efisiensi caching data master secara massal.")
        if st.button("🚀 Buat & Unduh Data Bundle"):
            bundle_bytes, err = generate_firestore_data_bundle()
            if err:
                st.error(err)
            else:
                st.download_button(
                    label="💾 Unduh Data Bundle (.bin)",
                    data=bundle_bytes,
                    file_name="lms_master_data.bundle",
                    mime="application/octet-stream"
                )

# ==========================================
# 9. PANEL GURU
# ==========================================
def render_guru():
    st.title("👨‍🏫 Panel Guru Pendidikan Pancasila")
    
    kelas_ajar = user_info.get("kelas_ajar", [])
    if isinstance(kelas_ajar, str): kelas_ajar = [kelas_ajar]
    
    if not kelas_ajar:
        st.warning("⚠️ Anda belum ditugaskan mengajar di kelas manapun. Hubungi Super Admin.")
        st.stop()

    t_dash, t_materi, t_tugas, t_koreksi, t_analisis, t_rekap = st.tabs([
        "📊 Dashboard", "📚 Bank Materi", "📝 Bank Tugas/Soal", "🔍 Koreksi & Penilaian", "📈 Analisis & Validitas (PG)", "📋 Rekap Nilai"
    ])

    with t_dash:
        st.subheader("📊 Ringkasan LMS")
        stats = get_guru_dashboard_stats(tuple(kelas_ajar))
        c1, c2, c3, c4 = st.columns(4)
        c1.metric("Total Siswa Ajar", f"{stats['total_siswa']} Siswa")
        c2.metric("Total Tugas Dibuat", f"{stats['total_tugas']} Tugas")
        c3.metric("Total Materi Dibuat", f"{stats['total_materi']} Materi")
        c4.metric("Total Jawaban Masuk", f"{stats['total_submitted']} Pengerjaan")

    with t_materi:
        st.subheader("📚 Kelola Materi Pembelajaran")
        with st.form("form_add_materi"):
            m_judul = st.text_input("Judul Materi")
            m_deskripsi = st.text_area("Deskripsi / Isu Pembelajaran")
            m_link = st.text_input("Link Media / Video / PDF (Opsional)")
            m_target = st.multiselect("Target Kelas", kelas_ajar, default=kelas_ajar)
            if st.form_submit_button("Simpan Materi"):
                if m_judul and m_target:
                    db.collection("materi_pancasila").add({
                        "judul": m_judul,
                        "deskripsi": m_deskripsi,
                        "link": m_link,
                        "target_kelas": m_target,
                        "created_by": user_info["username"],
                        "created_at": firestore.SERVER_TIMESTAMP
                    })
                    clear_materi_cache()
                    st.success("✅ Materi berhasil disimpan!")
                    st.rerun()

        st.divider()
        materi_list = get_all_materi_cached()
        for m in materi_list:
            with st.expander(f"📖 {m.get('judul')} (Kelas: {', '.join(m.get('target_kelas', []))})"):
                st.write(m.get('deskripsi'))
                if m.get('link'): st.markdown(f"🔗 [Buka Link Media]({m.get('link')})")
                if st.button("🗑️ Hapus Materi", key=f"del_mat_{m['id']}"):
                    db.collection("materi_pancasila").document(m['id']).delete()
                    clear_materi_cache()
                    st.success("Materi dihapus.")
                    st.rerun()

    with t_tugas:
        st.subheader("📝 Buat Tugas / Ulangan")
        t_tipe = st.radio("Tipe Soal", ["Pilihan Ganda (PG)", "Essay"], horizontal=True)
        tipe_code = "pg" if "Pilihan Ganda" in t_tipe else "essay"
        
        with st.form("form_create_tugas"):
            t_judul = st.text_input("Judul Tugas / Ulangan")
            t_jenis = st.selectbox("Jenis Tugas", ["Ulangan Harian", "Tugas Mandiri", "Kuis"])
            t_target = st.multiselect("Target Kelas", kelas_ajar, default=kelas_ajar)
            
            st.markdown("### ❓ Input Soal")
            j_soal = st.number_input("Jumlah Soal", min_value=1, max_value=50, value=3)
            
            soal_input = []
            for i in range(j_soal):
                st.markdown(f"**Soal Nomor {i+1}**")
                q_txt = st.text_area(f"Pertanyaan {i+1}", key=f"q_{i}")
                if tipe_code == "pg":
                    o_a = st.text_input(f"Opsi A Soal {i+1}", key=f"oa_{i}")
                    o_b = st.text_input(f"Opsi B Soal {i+1}", key=f"ob_{i}")
                    o_c = st.text_input(f"Opsi C Soal {i+1}", key=f"oc_{i}")
                    o_d = st.text_input(f"Opsi D Soal {i+1}", key=f"od_{i}")
                    kunci_pilih = st.selectbox(f"Kunci Jawaban Soal {i+1}", ["A", "B", "C", "D"], key=f"k_{i}")
                    kunci_idx = ["A", "B", "C", "D"].index(kunci_pilih)
                    soal_input.append({
                        "pertanyaan": q_txt,
                        "opsi": [o_a, o_b, o_c, o_d],
                        "kunci": kunci_idx
                    })
                else:
                    soal_input.append({"pertanyaan": q_txt})
            
            if st.form_submit_button("🚀 Publikasikan Tugas"):
                if t_judul and t_target:
                    db.collection("tugas_pancasila").add({
                        "judul": t_judul,
                        "jenis_tugas": t_jenis,
                        "tipe": tipe_code,
                        "target_kelas": t_target,
                        "soal": soal_input,
                        "created_by": user_info["username"],
                        "created_at": firestore.SERVER_TIMESTAMP
                    })
                    clear_tugas_cache()
                    st.success("✅ Tugas berhasil dipublikasikan!")
                    st.rerun()

        st.divider()
        st.subheader("📋 Daftar Tugas Terdaftar")
        tugas_list = get_all_tugas_cached()
        for tg in tugas_list:
            with st.expander(f"📌 {tg.get('judul')} ({tg.get('tipe').upper()}) - Kelas: {', '.join(tg.get('target_kelas', []))}"):
                st.write(f"Jumlah Soal: **{len(tg.get('soal', []))}**")
                if st.button("🗑️ Hapus Tugas Ini", key=f"del_tg_{tg['id']}"):
                    delete_tugas_and_submissions(tg['id'])
                    st.success("Tugas dan data pengerjaan siswa telah dihapus.")
                    st.rerun()

    with t_koreksi:
        st.subheader("🔍 Koreksi & Penilaian Siswa")
        sel_k_koreksi = st.selectbox("Pilih Kelas", kelas_ajar, key="sel_k_koreksi")
        tugas_kelas = [t for t in get_all_tugas_cached() if sel_k_koreksi in t.get("target_kelas", [])]
        
        if tugas_kelas:
            sel_tugas = st.selectbox("Pilih Tugas / Ulangan", tugas_kelas, format_func=lambda x: f"{x.get('judul')} ({x.get('tipe').upper()})", key="sel_t_koreksi")
            if sel_tugas:
                sub_list = get_pengerjaan_by_tugas_kelas_cached(sel_tugas["id"], sel_k_koreksi)
                all_siswa_kelas = get_siswa_by_kelas_cached(sel_k_koreksi)
                
                submitted_un = {s.get("username_siswa") for s in sub_list if s.get("status") == "submitted"}
                unsubmitted_siswa = [s for s in all_siswa_kelas if s["username"] not in submitted_un]
                
                col_k1, col_k2 = st.columns(2)
                with col_k1:
                    st.info(f"📊 Sudah Mengumpulkan: **{len(submitted_un)} / {len(all_siswa_kelas)}** Siswa")
                with col_k2:
                    if unsubmitted_siswa:
                        if st.button("🚨 Submit Paksa Semua Siswa Belum Mengumpulkan", type="primary"):
                            submit_jawaban_bulk(sel_tugas, unsubmitted_siswa, sel_k_koreksi)
                            st.success("Berhasil melakukan submit paksa untuk siswa yang belum mengumpulkan!")
                            st.rerun()

                st.divider()
                if sub_list:
                    for sub in sub_list:
                        with st.expander(f"👤 {sub.get('nama_siswa')} (@{sub.get('username_siswa')}) - Status: {sub.get('status').upper()} - Nilai: {sub.get('nilai', 'Belum Dinilai')}"):
                            soal_items = sub.get("soal", sel_tugas.get("soal", []))
                            jawaban_items = sub.get("jawaban", [])
                            
                            for idx, (q, a) in enumerate(zip(soal_items, jawaban_items), 1):
                                q_text = q.get('pertanyaan') if isinstance(q, dict) else q
                                st.write(f"**{idx}. {q_text}**")
                                if sub.get("tipe") == "pg":
                                    opsi_list = q.get("opsi", [])
                                    ans_idx = a if isinstance(a, int) else 0
                                    ans_text = opsi_list[ans_idx] if (isinstance(ans_idx, int) and 0 <= ans_idx < len(opsi_list)) else str(a)
                                    is_correct = (ans_idx == q.get("kunci", 0))
                                    st.write(f"Jawaban Siswa: **{ans_text}** ({'✅ Benar' if is_correct else '❌ Salah'})")
                                else:
                                    st.info(a or "(Siswa tidak menjawab)")

                            st.divider()
                            if sub.get("tipe") == "essay":
                                if st.button("🤖 Koreksi Otomatis dengan AI Gemini", key=f"ai_{sub['id']}"):
                                    with st.spinner("AI sedang mengoreksi jawaban..."):
                                        ai_score, ai_fb = koreksi_essay_dengan_ai(soal_items, jawaban_items)
                                        if ai_score is not None:
                                            db.collection("pengerjaan_siswa").document(sub['id']).update({
                                                "nilai": ai_score,
                                                "catatan_guru": f"🤖 AI Feedback: {ai_fb}",
                                                "updated_at": firestore.SERVER_TIMESTAMP
                                            })
                                            clear_pengerjaan_cache()
                                            st.success(f"✅ AI Menilai: {ai_score}")
                                            st.rerun()
                                        else:
                                            st.error(ai_fb)

                            with st.form(f"form_grade_{sub['id']}"):
                                new_val = st.number_input("Nilai Manual (0-100)", min_value=0, max_value=100, value=int(sub.get("nilai") or 0))
                                new_note = st.text_area("Catatan Guru", value=sub.get("catatan_guru", ""))
                                if st.form_submit_button("💾 Simpan Nilai"):
                                    db.collection("pengerjaan_siswa").document(sub['id']).update({
                                        "nilai": new_val,
                                        "catatan_guru": new_note,
                                        "status": "submitted",
                                        "updated_at": firestore.SERVER_TIMESTAMP
                                    })
                                    clear_pengerjaan_cache()
                                    st.success("Nilai berhasil disimpan!")
                                    st.rerun()

                            if st.button("🔄 Reset Pengerjaan Siswa Ini", key=f"rst_{sub['id']}"):
                                reset_pengerjaan_siswa(sub.get("username_siswa"), sel_tugas["id"])
                                st.success("Pengerjaan siswa di-reset!")
                                st.rerun()

    with t_analisis:
        st.subheader("📈 Analisis Butir Soal & Uji Validitas (PG)")
        sel_k_an = st.selectbox("Pilih Kelas", kelas_ajar, key="sel_k_an")
        tugas_pg = [t for t in get_all_tugas_cached() if sel_k_an in t.get("target_kelas", []) and t.get("tipe") == "pg"]
        
        if tugas_pg:
            selected_tugas = st.selectbox("Pilih Ulangan PG", tugas_pg, format_func=lambda x: x.get("judul"), key="sel_t_an")
            if selected_tugas:
                sub_list = get_pengerjaan_by_tugas_kelas_cached(selected_tugas["id"], sel_k_an)
                submitted_docs = [s for s in sub_list if s.get("status") == "submitted"]
                
                if not submitted_docs:
                    st.info("ℹ️ Belum ada siswa yang mengumpulkan tugas ini.")
                else:
                    soal_master = selected_tugas.get("soal", [])
                    total_responden = len(submitted_docs)
                    scores = [s.get("nilai", 0) for s in submitted_docs if s.get("nilai") is not None]

                    m1, m2, m3, m4 = st.columns(4)
                    m1.metric("Total Responden", f"{total_responden} Siswa")
                    m2.metric("Rata-rata Kelas", f"{round(sum(scores)/len(scores), 1) if scores else 0}")
                    m3.metric("Nilai Tertinggi", f"{max(scores) if scores else 0}")
                    m4.metric("Nilai Terendah", f"{min(scores) if scores else 0}")

                    st.divider()

                    matrix_data = []
                    for sub in submitted_docs:
                        row_scores = {}
                        for idx, q in enumerate(soal_master):
                            is_corr, _ = get_student_ans_for_master_q(sub, idx, q)
                            row_scores[f"Q_{idx}"] = is_corr
                        row_scores["Total_Benar"] = sum(row_scores.values())
                        matrix_data.append(row_scores)

                    df_matrix = pd.DataFrame(matrix_data)
                    analisis_rows = []

                    for idx, q in enumerate(soal_master, 1):
                        q_text = q.get("pertanyaan", "") if isinstance(q, dict) else str(q)
                        kunci_idx = q.get("kunci", 0) if isinstance(q, dict) else 0
                        kunci_str = ['A', 'B', 'C', 'D'][kunci_idx] if 0 <= kunci_idx <= 3 else "A"

                        counts = [0, 0, 0, 0]
                        jml_benar = 0
                        for sub in submitted_docs:
                            is_corr, opt_idx = get_student_ans_for_master_q(sub, idx - 1, q)
                            if is_corr == 1:
                                jml_benar += 1
                            if opt_idx is not None and isinstance(opt_idx, int) and 0 <= opt_idx <= 3:
                                counts[opt_idx] += 1

                        pct_benar = round((jml_benar / total_responden) * 100, 1) if total_responden > 0 else 0

                        if pct_benar >= 80: kategori_kesukaran = "🟢 Mudah"
                        elif pct_benar >= 30: kategori_kesukaran = "🟡 Sedang"
                        else: kategori_kesukaran = "🔴 Sukar"

                        q_col = f"Q_{idx-1}"
                        validity_status = "⚪ N/A"
                        
                        if total_responden >= 3 and q_col in df_matrix.columns:
                            std_q, std_tot = df_matrix[q_col].std(), df_matrix["Total_Benar"].std()
                            if std_q > 0 and std_tot > 0:
                                r_val = round(df_matrix[q_col].corr(df_matrix["Total_Benar"]), 3)
                                if pd.isna(r_val): r_val = 0.0
                                if r_val >= 0.30: validity_status = f"🟢 Valid ({r_val})"
                                elif r_val >= 0.20: validity_status = f"🟡 Cukup ({r_val})"
                                else: validity_status = f"🔴 Tidak Valid ({r_val})"
                            else: validity_status = "⚪ Varian 0"
                        else: validity_status = "⚪ Min. 3 Responden"

                        analisis_rows.append({
                            "No": idx,
                            "Soal": q_text[:50] + ("..." if len(q_text) > 50 else ""),
                            "Kunci Master": kunci_str,
                            "Benar": f"{jml_benar}/{total_responden}",
                            "% Benar": f"{pct_benar}%",
                            "Tingkat Kesukaran": kategori_kesukaran,
                            "Status Validitas (r)": validity_status,
                            "Distribusi Opsi Master (A | B | C | D)": f"A: {counts[0]} | B: {counts[1]} | C: {counts[2]} | D: {counts[3]}"
                        })

                    st.dataframe(pd.DataFrame(analisis_rows), use_container_width=True)

    with t_rekap:
        st.subheader("📋 Rekap Nilai Siswa")
        sel_k_rekap = st.selectbox("Pilih Kelas Rekap", kelas_ajar, key="sel_k_rekap")
        pengerjaan_kelas = get_all_pengerjaan_by_kelas_cached(sel_k_rekap)
        siswa_kelas = get_siswa_by_kelas_cached(sel_k_rekap)
        
        if siswa_kelas:
            rekap_data = []
            for s in siswa_kelas:
                s_un = s["username"]
                s_pengerjaan = [p for p in pengerjaan_kelas if p.get("username_siswa") == s_un]
                row = {
                    "Username": s_un,
                    "Nama Siswa": s.get("nama", s_un),
                    "Kelas": sel_k_rekap,
                    "Total Tugas Dikerjakan": len(s_pengerjaan),
                    "Rata-rata Nilai": round(sum(p.get("nilai", 0) for p in s_pengerjaan if p.get("nilai") is not None) / max(1, len([p for p in s_pengerjaan if p.get("nilai") is not None])), 1)
                }
                rekap_data.append(row)
            
            df_rekap = pd.DataFrame(rekap_data)
            st.dataframe(df_rekap, use_container_width=True)
            
            # Export CSV
            csv_buf = io.StringIO()
            df_rekap.to_csv(csv_buf, index=False)
            st.download_button(
                label="📥 Unduh Rekap Nilai CSV",
                data=csv_buf.getvalue(),
                file_name=f"Rekap_Nilai_{sel_k_rekap}.csv",
                mime="text/csv"
            )

# ==========================================
# 10. PANEL SISWA (INTEGRASI SOAL TERACAK)
# ==========================================
def render_siswa():
    username_s = user_info["username"]
    nama_s = user_info["nama"]
    kelas_s = user_info.get("kelas", "")

    if not kelas_s:
        st.error("⚠️ Anda belum terdaftar dalam kelas apapun. Hubungi Guru atau Super Admin.")
        st.stop()

    st.markdown(f"""
        <div class="student-header">
            <h2 style="margin:0; color:white;">🇮🇩 LMS Pendidikan Pancasila</h2>
            <p style="margin:5px 0 0 0; opacity:0.9;">Selamat datang, <b>{nama_s}</b> ({kelas_s})</p>
        </div>
    """, unsafe_allow_html=True)

    # A. MODE PENGERJAAN ACTIVE QUIZ
    active_quiz_id = st.session_state.get("active_quiz_id")

    if active_quiz_id:
        if "active_quiz_data" not in st.session_state or st.session_state["active_quiz_data"]["id"] != active_quiz_id:
            tugas_siswa_active = get_tugas_by_kelas_server_side(kelas_s, limit=50)
            st.session_state["active_quiz_data"] = next((t for t in tugas_siswa_active if t["id"] == active_quiz_id), None)

        tg = st.session_state.get("active_quiz_data")
        if not tg:
            st.session_state["active_quiz_id"] = None
            st.rerun()

        tg_id = tg["id"]
        doc_ref = db.collection("pengerjaan_siswa").document(f"{username_s}_{tg_id}")

        # Inisialisasi Sesi Soal & Jawaban (Dengan Pengacakan Ter-persis)
        if f"quiz_loaded_{tg_id}" not in st.session_state:
            doc_snap = doc_ref.get()
            existing_sub = doc_snap.to_dict() if doc_snap.exists else {}

            # Cek apakah sudah ada soal teracak yang tersimpan di Firestore untuk siswa ini
            saved_soal = existing_sub.get("soal")
            if isinstance(saved_soal, list) and len(saved_soal) > 0:
                soal_list = saved_soal
            else:
                # ACAK SOAL DAN OPSI JAWABAN
                soal_list = randomize_soal(tg.get("soal", []), tipe=tg.get("tipe", "pg"))

            st.session_state[f"quiz_soal_{tg_id}"] = soal_list
            total_soal = len(soal_list)

            saved_ans = existing_sub.get("jawaban")
            if isinstance(saved_ans, list) and len(saved_ans) == total_soal:
                st.session_state[f"quiz_answers_{tg_id}"] = saved_ans
            else:
                st.session_state[f"quiz_answers_{tg_id}"] = [None] * total_soal

            v_count = existing_sub.get("violation_count", 0)
            ijin_val = existing_sub.get("ijin_guru", True)

            if f"quiz_session_active_{tg_id}" not in st.session_state:
                if existing_sub.get("status") == "in_progress":
                    v_count += 1
                    if v_count >= 15:
                        tg_submit = dict(tg)
                        tg_submit["soal"] = soal_list
                        submit_jawaban_siswa(tg_submit, username_s, nama_s, kelas_s, st.session_state[f"quiz_answers_{tg_id}"], is_violation=True)
                        st.session_state["active_quiz_id"] = None
                        clear_pengerjaan_cache()
                        st.rerun()
                    elif v_count == 10:
                        ijin_val = False

                    doc_ref.set({
                        "username_siswa": username_s,
                        "nama_siswa": nama_s,
                        "kelas_siswa": kelas_s,
                        "id_tugas": tg_id,
                        "status": "in_progress",
                        "ijin_guru": ijin_val,
                        "violation_count": v_count,
                        "soal": soal_list,
                        "jawaban": st.session_state[f"quiz_answers_{tg_id}"],
                        "updated_at": firestore.SERVER_TIMESTAMP
                    }, merge=True)
                    clear_pengerjaan_cache()
                else:
                    doc_ref.set({
                        "username_siswa": username_s,
                        "nama_siswa": nama_s,
                        "kelas_siswa": kelas_s,
                        "id_tugas": tg_id,
                        "status": "in_progress",
                        "ijin_guru": True,
                        "violation_count": v_count,
                        "soal": soal_list,
                        "jawaban": st.session_state[f"quiz_answers_{tg_id}"],
                        "updated_at": firestore.SERVER_TIMESTAMP
                    }, merge=True)
                    clear_pengerjaan_cache()

            st.session_state[f"violation_count_{tg_id}"] = v_count
            st.session_state[f"ijin_guru_{tg_id}"] = ijin_val
            st.session_state[f"quiz_session_active_{tg_id}"] = True
            st.session_state[f"quiz_page_{tg_id}"] = 0
            st.session_state[f"quiz_loaded_{tg_id}"] = True

        soal_list = st.session_state.get(f"quiz_soal_{tg_id}", tg.get("soal", []))
        total_soal = len(soal_list)
        curr_page = st.session_state.get(f"quiz_page_{tg_id}", 0)
        answers = st.session_state[f"quiz_answers_{tg_id}"]

        st.subheader(f"📝 {tg.get('judul')} ({tg.get('tipe').upper()})")
        st.caption("🎲 *Soal dan Opsi Jawaban disajikan dalam urutan teracak.*")
        st.progress((curr_page + 1) / max(1, total_soal))

        if 0 <= curr_page < total_soal:
            sq = soal_list[curr_page]
            q_text = sq.get("pertanyaan", "") if isinstance(sq, dict) else str(sq)

            st.markdown(f"### **Soal {curr_page + 1} dari {total_soal}**")
            st.write(f"#### {q_text}")

            if tg.get("tipe") == "pg":
                opsi_list = sq.get("opsi", [])
                curr_ans = answers[curr_page]
                
                # Menentukan indeks pilihan awal
                default_idx = curr_ans if (curr_ans is not None and isinstance(curr_ans, int) and 0 <= curr_ans < len(opsi_list)) else None
                
                sel_opt = st.radio(
                    "Pilih Jawaban Anda:",
                    options=list(range(len(opsi_list))),
                    format_func=lambda i: f"{chr(65+i)}. {opsi_list[i]}",
                    index=default_idx,
                    key=f"radio_{tg_id}_{curr_page}"
                )

                if sel_opt != curr_ans:
                    answers[curr_page] = sel_opt
                    st.session_state[f"quiz_answers_{tg_id}"] = answers
                    doc_ref.set({
                        "jawaban": answers,
                        "soal": soal_list,
                        "updated_at": firestore.SERVER_TIMESTAMP
                    }, merge=True)
            else:
                curr_ans_txt = answers[curr_page] or ""
                ans_txt = st.text_area("Tuliskan Jawaban Anda:", value=curr_ans_txt, key=f"essay_{tg_id}_{curr_page}")
                if ans_txt != curr_ans_txt:
                    answers[curr_page] = ans_txt
                    st.session_state[f"quiz_answers_{tg_id}"] = answers
                    doc_ref.set({
                        "jawaban": answers,
                        "soal": soal_list,
                        "updated_at": firestore.SERVER_TIMESTAMP
                    }, merge=True)

        st.divider()
        col_b1, col_b2, col_b3 = st.columns([2, 4, 2])

        with col_b1:
            if curr_page > 0:
                if st.button("⬅️ Sebelumnya", key=f"prev_{curr_page}"):
                    st.session_state[f"quiz_page_{tg_id}"] = curr_page - 1
                    st.rerun()

        with col_b3:
            if curr_page < total_soal - 1:
                if st.button("Berikutnya ➡️", key=f"next_{curr_page}"):
                    st.session_state[f"quiz_page_{tg_id}"] = curr_page + 1
                    st.rerun()
            else:
                if st.button("✅ Selesai & Kirim Jawaban", type="primary", key=f"submit_{curr_page}"):
                    tg_submit = dict(tg)
                    tg_submit["soal"] = soal_list
                    submit_jawaban_siswa(
                        tg_submit, username_s, nama_s, kelas_s, 
                        st.session_state[f"quiz_answers_{tg_id}"]
                    )
                    st.session_state["active_quiz_id"] = None
                    st.success("🎉 Jawaban berhasil dikirim!")
                    st.rerun()

        st.divider()
        st.markdown("### 📌 Navigasi Soal")
        num_cols = min(10, total_soal) if total_soal > 0 else 1
        cols = st.columns(num_cols)
        for idx_q in range(total_soal):
            ans_val = answers[idx_q]
            is_answered = (ans_val is not None and ans_val != "" and ans_val != -1)
            btn_label = f"{'🟢' if is_answered else '⚪'} {idx_q + 1}"
            col_idx = idx_q % num_cols
            with cols[col_idx]:
                if st.button(btn_label, key=f"nav_btn_{idx_q}"):
                    st.session_state[f"quiz_page_{tg_id}"] = idx_q
                    st.rerun()

        return

    # B. DASHBOARD UTAMA SISWA
    t_list_tugas, t_materi_s, t_riwayat = st.tabs([
        "📝 Daftar Tugas / Ulangan", "📚 Materi Pembelajaran", "📊 Riwayat & Nilai"
    ])

    user_pengerjaan = get_user_pengerjaan_cached(username_s)

    with t_list_tugas:
        st.subheader("📝 Daftar Tugas / Ulangan Tersedia")
        tugas_kelas_s = get_tugas_by_kelas_server_side(kelas_s, limit=50)

        if not tugas_kelas_s:
            st.info("ℹ️ Belum ada tugas / ulangan untuk kelas Anda.")
        else:
            for tg in tugas_kelas_s:
                tg_id = tg["id"]
                p_info = user_pengerjaan.get(tg_id, {})
                status_p = p_info.get("status", "not_started")

                with st.expander(f"📌 {tg.get('judul')} ({tg.get('jenis_tugas')}) - Status: {status_p.upper()}"):
                    st.write(f"Tipe Soal: **{tg.get('tipe').upper()}** | Jumlah Soal: **{len(tg.get('soal', []))}**")

                    if status_p == "submitted":
                        st.success(f"✅ Sudah Dikerjakan. Nilai: **{p_info.get('nilai', 'Belum Dinilai')}**")
                        if p_info.get("catatan_guru"):
                            st.info(f"💬 Catatan Guru: {p_info.get('catatan_guru')}")
                    elif status_p == "in_progress":
                        if st.button("▶️ Lanjutkan Mengerjakan", key=f"start_{tg_id}"):
                            st.session_state["active_quiz_id"] = tg_id
                            st.rerun()
                    else:
                        if st.button("🚀 Mulai Mengerjakan", type="primary", key=f"start_{tg_id}"):
                            st.session_state["active_quiz_id"] = tg_id
                            st.rerun()

    with t_materi_s:
        st.subheader("📚 Materi Pembelajaran Kelas")
        materi_siswa = get_materi_by_kelas_server_side(kelas_s, limit=50)
        if not materi_siswa:
            st.info("ℹ️ Belum ada materi untuk kelas Anda.")
        else:
            for m in materi_siswa:
                with st.expander(f"📖 {m.get('judul')}"):
                    st.write(m.get('deskripsi'))
                    if m.get('link'): st.markdown(f"🔗 [Buka Link Media / Video]({m.get('link')})")

    with t_riwayat:
        st.subheader("📊 Riwayat & Nilai Pengerjaan")
        if not user_pengerjaan:
            st.info("Belum ada riwayat pengerjaan.")
        else:
            rows_riwayat = []
            for t_id, p in user_pengerjaan.items():
                rows_riwayat.append({
                    "Judul Tugas": p.get("judul_tugas", t_id),
                    "Tipe": str(p.get("tipe", "")).upper(),
                    "Status": str(p.get("status", "")).upper(),
                    "Nilai": p.get("nilai", "Belum Dinilai"),
                    "Catatan Guru": p.get("catatan_guru", "-")
                })
            st.dataframe(pd.DataFrame(rows_riwayat), use_container_width=True)

# ==========================================
# 11. MAIN ROUTER
# ==========================================
if role == "superadmin":
    render_superadmin()
elif role == "guru":
    render_guru()
elif role == "siswa":
    render_siswa()
