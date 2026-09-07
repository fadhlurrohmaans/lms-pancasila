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

try:
    from google.cloud.firestore_bundle import FirestoreBundle
except ImportError:
    try:
        from google.cloud.firestore_v1.bundle import FirestoreBundle
    except ImportError:
        FirestoreBundle = None

# ==========================================
# 1. CONFIG & UT BRANDING STYLING
# ==========================================
st.set_page_config(
    page_title="Tuton UT - Pendidikan Pancasila",
    page_icon="🎓",
    layout="wide",
    initial_sidebar_state="collapsed"
)

st.markdown("""
    <style>
    html, body, [class*="css"] {
        font-family: -apple-system, BlinkMacSystemFont, "Segoe UI", Roboto, Helvetica, Arial, sans-serif;
    }
    :root {
        --ut-navy: #002147;
        --ut-yellow: #FFC72C;
        --ut-light-bg: #F4F6F9;
    }
    input[type="text"], input[type="password"], textarea, select { 
        font-size: 15px !important;
        border-radius: 8px !important;
    }
    @media (max-width: 768px) {
        .main .block-container { padding: 0.8rem 0.6rem 3rem !important; }
        [data-testid="column"] { width: 100% !important; flex: 1 1 100% !important; min-width: 100% !important; margin-bottom: 0.5rem; }
        .stButton > button, .stDownloadButton > button { 
            width: 100% !important; min-height: 48px !important; font-size: 15px !important; font-weight: bold; border-radius: 10px !important; 
        }
        h1 { font-size: 1.5rem !important; } 
        h2 { font-size: 1.2rem !important; } 
    }
    .ut-header {
        background: linear-gradient(135deg, #002147 0%, #003366 100%);
        border-bottom: 5px solid #FFC72C;
        color: white; padding: 20px 24px; border-radius: 12px; margin-bottom: 20px;
        box-shadow: 0 4px 12px rgba(0,0,0,0.1);
    }
    .ut-card {
        background: white; border: 1px solid #e2e8f0; border-radius: 10px; padding: 16px; margin-bottom: 12px;
        border-left: 4px solid #002147;
    }
    .stTabs [data-baseweb="tab-list"] { gap: 8px; overflow-x: auto; white-space: nowrap; border-bottom: 2px solid #eaeaea; padding-bottom: 4px; }
    .stTabs [data-baseweb="tab"] { padding: 8px 16px; border-radius: 16px; font-weight: 600; font-size: 14px; }
    .stTabs [aria-selected="true"] { background-color: #002147 !important; color: #FFC72C !important; }
    
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

# --- FIRESTORE AGGREGATION QUERIES (.count()) ---
@st.cache_data(ttl=60)
def count_siswa_by_kelas(kelas):
    try:
        query = db.collection("users").where("role", "==", "siswa").where("kelas", "==", kelas)
        return query.count().get()[0][0].value
    except Exception: return 0

@st.cache_data(ttl=60)
def count_all_users(role_filter=None):
    try:
        query = db.collection("users")
        if role_filter and role_filter != "semua":
            query = query.where("role", "==", role_filter)
        return query.count().get()[0][0].value
    except Exception: return 0

@st.cache_data(ttl=60)
def count_submitted_by_tugas_kelas(tugas_id, kelas):
    try:
        query = db.collection("pengerjaan_siswa").where("id_tugas", "==", tugas_id).where("kelas_siswa", "==", kelas).where("status", "==", "submitted")
        return query.count().get()[0][0].value
    except Exception: return 0

@st.cache_data(ttl=60)
def get_guru_dashboard_stats(pilihan_kelas_tuple):
    total_materi = db.collection("materi_pancasila").count().get()[0][0].value if db else 0
    total_tugas = db.collection("tugas_pancasila").count().get()[0][0].value if db else 0
    total_siswa = sum(count_siswa_by_kelas(k) for k in pilihan_kelas_tuple)
    total_submitted = 0
    for k in pilihan_kelas_tuple:
        try:
            total_submitted += db.collection("pengerjaan_siswa").where("kelas_siswa", "==", k).where("status", "==", "submitted").count().get()[0][0].value
        except Exception: pass
    return {"total_siswa": total_siswa, "total_tugas": total_tugas, "total_materi": total_materi, "total_submitted": total_submitted}

# --- CACHED READS WITH LIMITS & PAGINATION ---
@st.cache_data(ttl=86400)
def ensure_default_admin_created():
    admin_ref = db.collection("users").document("admin")
    if not admin_ref.get().exists:
        admin_ref.set({
            "nama": "Administrator Tuton",
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
    return sorted(doc.to_dict().get("daftar", [])) if doc.exists else []

@st.cache_data(ttl=300)
def get_users_paginated(limit=10, offset=0, role_filter=None):
    query = db.collection("users")
    if role_filter and role_filter != "semua":
        query = query.where("role", "==", role_filter)
    docs = query.limit(limit).offset(offset).stream()
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

@st.cache_data(ttl=30)
def get_user_pengerjaan_cached(username, limit=50):
    docs = db.collection("pengerjaan_siswa").where("username_siswa", "==", username).limit(limit).stream()
    return {d.to_dict().get("id_tugas"): {"id": d.id, **d.to_dict()} for d in docs}

@st.cache_data(ttl=30)
def get_pengerjaan_by_tugas_kelas_cached(tugas_id, kelas, limit=150, offset=0):
    docs = db.collection("pengerjaan_siswa").where("id_tugas", "==", tugas_id).where("kelas_siswa", "==", kelas).limit(limit).offset(offset).stream()
    return [{"id": d.id, **d.to_dict()} for d in docs]

@st.cache_data(ttl=30)
def get_all_pengerjaan_by_kelas_cached(kelas, limit=300):
    docs = db.collection("pengerjaan_siswa").where("kelas_siswa", "==", kelas).limit(limit).stream()
    return [d.to_dict() for d in docs]

# --- TUTON SPECIFIC CACHED READS ---
@st.cache_data(ttl=60)
def get_diskusi_by_sesi_kelas(sesi, kelas):
    docs = db.collection("diskusi_pancasila").where("sesi", "==", int(sesi)).where("kelas", "==", kelas).stream()
    return [{"id": d.id, **d.to_dict()} for d in docs]

@st.cache_data(ttl=60)
def get_kehadiran_user(username):
    docs = db.collection("kehadiran_siswa").where("username", "==", username).stream()
    return {d.to_dict().get("sesi"): d.to_dict() for d in docs}

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

def clear_diskusi_cache():
    get_diskusi_by_sesi_kelas.clear()

def clear_kehadiran_cache():
    get_kehadiran_user.clear()

# ==========================================
# 3. FIRESTORE DATA BUNDLES
# ==========================================
@st.cache_data(ttl=3600)
def generate_firestore_data_bundle():
    if FirestoreBundle is None:
        return None, "Modul `google.cloud.firestore_bundle` tidak tersedia."
    try:
        bundle = FirestoreBundle("tuton_master_bundle")
        kelas_snap = db.collection("config").document("master_kelas").get()
        if kelas_snap.exists: bundle.add_document(kelas_snap)
        bundle.add_named_query("bundle_all_materi", db.collection("materi_pancasila").limit(50)._query())
        bundle.add_named_query("bundle_all_tugas", db.collection("tugas_pancasila").limit(50)._query())
        return bundle.build(), None
    except Exception as e:
        return None, f"Gagal membuat Data Bundle: {str(e)}"

# ==========================================
# 4. UTILITIES
# ==========================================
def render_pagination_controls(total_items, default_page_size=10, key_prefix="pg"):
    if total_items <= 0: return 1, default_page_size, 0
    col_p1, col_p2, col_p3 = st.columns([2, 2, 4])
    with col_p1:
        page_size = st.selectbox("Per Halaman", options=[5, 10, 20, 50], index=1, key=f"{key_prefix}_size")
    total_pages = max(1, (total_items + page_size - 1) // page_size)
    with col_p2:
        curr_page = st.number_input(f"Halaman (1-{total_pages})", min_value=1, max_value=total_pages, value=1, key=f"{key_prefix}_num")
    offset = (curr_page - 1) * page_size
    with col_p3:
        st.markdown(f"<p style='padding-top:25px; color:#666;'>Data {offset+1}-{min(offset+page_size, total_items)} dari {total_items}</p>", unsafe_allow_html=True)
    return curr_page, page_size, offset

def safe_read_uploaded_file(uploaded_file):
    if uploaded_file.name.endswith('.csv'):
        for enc in ['utf-8', 'utf-8-sig', 'latin1']:
            try:
                uploaded_file.seek(0)
                return pd.read_csv(uploaded_file, encoding=enc)
            except Exception: continue
        uploaded_file.seek(0)
        return pd.read_csv(uploaded_file, encoding='utf-8', errors='replace')
    return pd.read_excel(uploaded_file)

def hash_pass(password):
    return hashlib.sha256(password.encode()).hexdigest()

def generate_username(nama, existing_usernames=None):
    if existing_usernames is None:
        existing_usernames = {u["id"] for u in get_all_users_cached()}
    first_name = nama.strip().split()[0] if nama.strip() else "mahasiswa"
    base_username = re.sub(r'[^a-z0-9]', '', first_name.lower())[:5] or "mhs"
    username, counter = base_username, 1
    while username in existing_usernames:
        username = f"{base_username}{counter}"
        counter += 1
    return username

def generate_password(length=6):
    return ''.join(random.choices(string.ascii_lowercase + string.digits, k=length))

def is_target_sesuai_kelas(doc_data, kelas_siswa):
    target = doc_data.get("target_kelas", [])
    if not target: return True
    return kelas_siswa in target if isinstance(target, list) else target == kelas_siswa

def submit_jawaban_siswa(tg, username_s, nama_s, kelas_s, answers, is_forced=False, is_violation=False):
    tg_id = tg["id"]
    soal_list = tg.get("soal", [])
    total_soal = len(soal_list)
    catatan = "⚠️ Submit Otomatis (Limit Pelanggaran)" if is_violation else ("Di-submit Paksa Tutor" if is_forced else "Penilaian Otomatis Sistem")
    doc_ref = db.collection("pengerjaan_siswa").document(f"{username_s}_{tg_id}")

    if tg.get("tipe") == "pg":
        correct_count = sum(1 for idx_q, sq in enumerate(soal_list) if idx_q < len(answers) and answers[idx_q] == sq.get("kunci"))
        score = round((correct_count / total_soal) * 100) if total_soal > 0 else 0
        doc_ref.set({
            "id_tugas": tg_id, "judul_tugas": tg.get("judul"), "username_siswa": username_s,
            "nama_siswa": nama_s, "kelas_siswa": kelas_s, "tipe": "pg", "jawaban": answers,
            "nilai": score, "catatan_guru": catatan, "status": "submitted", "ijin_guru": True,
            "submitted_at": firestore.SERVER_TIMESTAMP, "updated_at": firestore.SERVER_TIMESTAMP
        }, merge=True)
    else:
        doc_ref.set({
            "id_tugas": tg_id, "judul_tugas": tg.get("judul"), "username_siswa": username_s,
            "nama_siswa": nama_s, "kelas_siswa": kelas_s, "tipe": "essay", "soal": soal_list,
            "jawaban": answers, "nilai": None, "catatan_guru": catatan, "status": "submitted", "ijin_guru": True,
            "submitted_at": firestore.SERVER_TIMESTAMP, "updated_at": firestore.SERVER_TIMESTAMP
        }, merge=True)
    clear_pengerjaan_cache()
    return True

def reset_pengerjaan_siswa(username_siswa, tugas_id):
    db.collection("pengerjaan_siswa").document(f"{username_siswa}_{tugas_id}").delete()
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

# ==========================================
# 5. AI EVALUATION HELPER
# ==========================================
def koreksi_essay_dengan_ai(soal_list, jawaban_list):
    api_key = st.secrets.get("GEMINI_API_KEY") or st.secrets.get("gemini", {}).get("api_key") or st.secrets.get("firebase", {}).get("GEMINI_API_KEY")
    if not api_key: return None, "Key GEMINI_API_KEY belum dikonfigurasi."
    try:
        genai.configure(api_key=api_key)
        prompt_items = [f"Soal {i+1}: {s.get('pertanyaan','') if isinstance(s, dict) else str(s)}\nJawaban: {str(j).strip() or '(Kosong)'}" for i, (s, j) in enumerate(zip(soal_list, jawaban_list))]
        prompt = f"Jumlah Soal: {len(soal_list)}\n\n" + "\n\n".join(prompt_items) + '\n\nFormat JSON HANYA:\n{"nilai": 85, "feedback": "Catatan..."}'
        
        model = genai.GenerativeModel(model_name='gemini-2.0-flash', system_instruction="Evaluasi jawaban sebagai Tutor Pendidikan Pancasila UT. Berikan nilai integer 0-100 dan feedback singkat.")
        res = model.generate_content(prompt, generation_config={"response_mime_type": "application/json"})
        raw_text = re.sub(r'^```json\s*|\s*```$', '', res.text.strip())
        res_json = json.loads(raw_text)
        return int(res_json.get("nilai", 0)), str(res_json.get("feedback", "")).strip()
    except Exception as e:
        return None, f"Gagal AI: {str(e)}"

# ==========================================
# 6. AUTHENTICATION
# ==========================================
if "user" not in st.session_state:
    st.session_state["user"] = None

ensure_default_admin_created()

if st.session_state["user"] is None:
    st.markdown("""
        <div style="text-align: center; padding: 20px;">
            <h1 style="color: #002147; margin-bottom: 0;">🎓 TUTON UNIVERSITAS TERBUKA</h1>
            <p style="color: #666; font-weight: 600;">LMS Pendidikan Pancasila (MKDU4111)</p>
        </div>
    """, unsafe_allow_html=True)
    
    with st.form("form_login"):
        username = st.text_input("NIM / Username").strip().lower()
        password = st.text_input("Password", type="password")
        if st.form_submit_button("Masuk Ke Tuton UT"):
            if username and password:
                all_users = {u["id"]: u for u in get_all_users_cached(limit=500)}
                if username in all_users and all_users[username].get("password") == hash_pass(password):
                    u = all_users[username]
                    st.session_state["user"] = {
                        "username": username, "nama": u.get("nama"), "role": u.get("role"),
                        "kelas": u.get("kelas", ""), "kelas_ajar": u.get("kelas_ajar", [])
                    }
                    st.success(f"Selamat Datang, {u.get('nama')}!")
                    st.rerun()
                else: st.error("Username atau password salah!")
            else: st.warning("Silakan lengkapi NIM/Username & Password.")
    st.stop()

# ==========================================
# 7. SIDEBAR
# ==========================================
user_info = st.session_state["user"]
role = user_info["role"]
role_label = "Tutor" if role == "guru" else ("Mahasiswa" if role == "siswa" else "Super Admin")

st.sidebar.markdown(f"### 🎓 Tuton UT")
st.sidebar.caption(f"👤 **{user_info['nama']}**\n\nRole: **{role_label}** | @{user_info['username']}")
if role == "siswa" and user_info.get("kelas"):
    st.sidebar.caption(f"🏫 Kelas Tuton: **{user_info['kelas']}**")

if st.sidebar.button("🚪 Keluar / Logout"):
    st.session_state.clear()
    components.html("<script>sessionStorage.clear(); localStorage.clear();</script>", height=0)
    st.rerun()

st.sidebar.divider()

# ==========================================
# 8. PANEL SUPER ADMIN
# ==========================================
def render_superadmin():
    st.title("⚙️ Panel Administrator Tuton")
    t_kelas, t_list, t_add, t_imp, t_edit, t_del, t_bundle = st.tabs([
        "🏫 Kelas Tuton", "👥 User", "➕ Buat Akun", "📥 Import/Export", "✏️ Atur Kelas", "🗑️ Hapus Akun", "📦 Data Bundles"
    ])

    with t_kelas:
        st.subheader("🏫 Kelola Kelas Tutorial Online")
        daftar_kelas = get_all_kelas()
        col1, col2 = st.columns(2)
        with col1:
            for k in daftar_kelas: st.markdown(f"- 🏫 Kelas: **{k}**")
        with col2:
            with st.form("f_add_k", clear_on_submit=True):
                new_k = st.text_input("Nama Kelas Tuton Baru (cth: UT-01)").strip()
                if st.form_submit_button("Tambah Kelas"):
                    if new_k and new_k not in daftar_kelas:
                        db.collection("config").document("master_kelas").set({"daftar": sorted(daftar_kelas + [new_k])}, merge=True)
                        clear_kelas_cache()
                        st.success(f"Kelas {new_k} berhasil ditambahkan.")
                        st.rerun()

    with t_list:
        st.subheader("👥 Daftar Pengguna Sistem")
        role_filter = st.selectbox("Filter Peran", ["semua", "siswa", "guru", "superadmin"], format_func=lambda x: "Mahasiswa" if x=="siswa" else ("Tutor" if x=="guru" else x.upper()))
        total_users = count_all_users(role_filter)
        curr_page, limit, offset = render_pagination_controls(total_users, default_page_size=10, key_prefix="u_pg")
        
        paginated = get_users_paginated(limit=limit, offset=offset, role_filter=role_filter)
        u_table = [{"NIM/Username": u.get("id"), "Nama": u.get("nama"), "Peran": "Mahasiswa" if u.get("role")=="siswa" else ("Tutor" if u.get("role")=="guru" else "Admin"), "Kelas": u.get("kelas") or ", ".join(u.get("kelas_ajar", []))} for u in paginated]
        if u_table: st.dataframe(pd.DataFrame(u_table), use_container_width=True)

    with t_add:
        st.subheader("➕ Buat Akun Satuan")
        daftar_kelas = get_all_kelas()
        new_role_sel = st.selectbox("Peran User", ["Mahasiswa", "Tutor", "Superadmin"])
        new_role = "siswa" if new_role_sel == "Mahasiswa" else ("guru" if new_role_sel == "Tutor" else "superadmin")
        
        with st.form("form_add_user", clear_on_submit=True):
            nama = st.text_input("Nama Lengkap")
            uname = st.text_input("NIM / Username").strip().lower()
            pwd = st.text_input("Password", type="password")
            k_s = st.selectbox("Kelas Mahasiswa", options=daftar_kelas) if new_role == "siswa" else None
            k_g = st.multiselect("Kelas Ajar Tutor", options=daftar_kelas) if new_role == "guru" else None
            
            if st.form_submit_button("Buat Akun"):
                if nama and uname and pwd:
                    payload = {"nama": nama, "password": hash_pass(pwd), "password_plain": pwd, "role": new_role, "created_at": firestore.SERVER_TIMESTAMP}
                    if new_role == "siswa": payload["kelas"] = k_s
                    elif new_role == "guru": payload["kelas_ajar"] = k_g
                    db.collection("users").document(uname).set(payload)
                    clear_users_cache()
                    st.success("Akun berhasil dibuat!")
                    st.rerun()

    with t_imp:
        st.subheader("📥 Import Data Mahasiswa & Tutor")
        target_role_imp = st.radio("Peran Import:", ["Mahasiswa", "Tutor"], horizontal=True)
        up_file = st.file_uploader("Upload File (.csv / .xlsx)", type=["csv", "xlsx"])
        if up_file and st.button("🚀 Proses Import"):
            df = safe_read_uploaded_file(up_file)
            df.columns = [str(c).strip().lower() for c in df.columns]
            if "nama" in df.columns and "kelas" in df.columns:
                role_str = "siswa" if target_role_imp == "Mahasiswa" else "guru"
                cached_u = get_all_users_cached(limit=500)
                exist_names = {u.get("nama", "").strip().lower(): u["id"] for u in cached_u if u.get("role") == role_str}
                existing_un = {u["id"] for u in cached_u}
                
                for _, r in df.iterrows():
                    n_str, k_str = str(r["nama"]).strip(), str(r["kelas"]).strip()
                    if not n_str or pd.isna(r["nama"]): continue
                    
                    if role_str == "guru":
                        lk = [x.strip() for x in k_str.split(",") if x.strip()]
                        if n_str.lower() in exist_names:
                            db.collection("users").document(exist_names[n_str.lower()]).update({"kelas_ajar": lk})
                        else:
                            un = generate_username(n_str, existing_un)
                            existing_un.add(un)
                            pw = generate_password()
                            db.collection("users").document(un).set({"nama": n_str, "password": hash_pass(pw), "password_plain": pw, "role": "guru", "kelas_ajar": lk, "created_at": firestore.SERVER_TIMESTAMP})
                    else:
                        if n_str.lower() in exist_names:
                            db.collection("users").document(exist_names[n_str.lower()]).update({"kelas": k_str})
                        else:
                            un = generate_username(n_str, existing_un)
                            existing_un.add(un)
                            pw = generate_password()
                            db.collection("users").document(un).set({"nama": n_str, "password": hash_pass(pw), "password_plain": pw, "role": "siswa", "kelas": k_str, "created_at": firestore.SERVER_TIMESTAMP})
                clear_users_cache()
                st.success("Import Berhasil!")
                st.rerun()

    with t_edit:
        st.subheader("✏️ Edit Kelas User")
        cached_u = get_all_users_cached(limit=500)
        users_map = {u["id"]: f"{u.get('nama')} (@{u['id']})" for u in cached_u if u.get("role") in ["siswa", "guru"]}
        daftar_k = get_all_kelas()
        if users_map and daftar_k:
            target_uid = st.selectbox("Pilih User", list(users_map.keys()), format_func=lambda x: users_map[x])
            u_data = next(u for u in cached_u if u["id"] == target_uid)
            with st.form(f"f_edit_u_{target_uid}"):
                if u_data.get("role") == "siswa":
                    nk = st.selectbox("Kelas Baru", options=daftar_k)
                    if st.form_submit_button("Simpan Perubahan"):
                        db.collection("users").document(target_uid).update({"kelas": nk})
                        clear_users_cache(); st.success("Kelas diperbarui!"); st.rerun()
                else:
                    nka = st.multiselect("Kelas Ajar Baru", options=daftar_k, default=[k for k in u_data.get("kelas_ajar",[]) if k in daftar_k])
                    if st.form_submit_button("Simpan Perubahan"):
                        db.collection("users").document(target_uid).update({"kelas_ajar": nka})
                        clear_users_cache(); st.success("Kelas Ajar diperbarui!"); st.rerun()

    with t_del:
        st.subheader("🗑️ Hapus Akun User")
        all_u = {u["id"]: f"{u.get('nama')} (@{u['id']})" for u in get_all_users_cached() if u["id"] != user_info["username"]}
        if all_u:
            target_del = st.selectbox("Pilih User Dihapus", list(all_u.keys()), format_func=lambda x: all_u[x])
            if st.button("Hapus Akun", type="primary"):
                db.collection("users").document(target_del).delete()
                clear_users_cache(); st.success("Akun dihapus."); st.rerun()

    with t_bundle:
        st.subheader("📦 Generator Data Bundle")
        if st.button("🚀 Regenerate Data Bundle"):
            b_bytes, err = generate_firestore_data_bundle()
            if err: st.error(err)
            else:
                st.session_state["cached_bundle_bytes"] = b_bytes
                st.success("Bundle berhasil diperbarui!")

# ==========================================
# 9. PANEL TUTOR (GURU)
# ==========================================
def render_guru():
    st.markdown("""
        <div class="ut-header">
            <h2 style="margin:0; color:#FFC72C;">🎓 DASHBOARD TUTOR TUTON UT</h2>
            <p style="margin:0; font-size:14px;">Mata Kuliah: MKDU4111 / Pendidikan Pancasila</p>
        </div>
    """, unsafe_allow_html=True)
    
    pilihan_kelas = user_info.get("kelas_ajar") or get_all_kelas()
    if isinstance(pilihan_kelas, str): pilihan_kelas = [pilihan_kelas]

    guru_stats = get_guru_dashboard_stats(tuple(pilihan_kelas))
    st.markdown("##### ⚡ Ringkasan Statistik Aktivitas Tuton")
    m1, m2, m3, m4 = st.columns(4)
    m1.metric("👥 Total Mahasiswa", f"{guru_stats['total_siswa']}")
    m2.metric("📝 Total Tugas", f"{guru_stats['total_tugas']}")
    m3.metric("📖 Inisiasi Materi", f"{guru_stats['total_materi']}")
    m4.metric("💬 Pengumpulan Tugas", f"{guru_stats['total_submitted']}")
    st.divider()

    menu = st.sidebar.radio("📌 Menu Tutor", ["📖 Inisiasi Sesi", "💬 Forum Diskusi", "📝 Tugas Tuton", "📊 Rekapitulasi & Nilai UT"])

    if menu == "📖 Inisiasi Sesi":
        st.header("📖 Kelola Materi Inisiasi (Sesi 1 - 8)")
        t_list, t_buat = st.tabs(["📋 Daftar Inisiasi", "➕ Tambah Inisiasi Sesi"])
        
        with t_list:
            materi_docs = get_all_materi_cached(limit=50)
            if not materi_docs: st.info("Belum ada materi inisiasi diunggah.")
            else:
                for m in materi_docs:
                    m_id = m["id"]
                    sesi_num = m.get("sesi", 1)
                    with st.container(border=True):
                        st.markdown(f"### 📘 [SESI {sesi_num}] {m.get('judul')}")
                        st.caption(f"Target Kelas: {', '.join(m.get('target_kelas', [])) or 'Semua'}")
                        if m.get("konten"): st.write(m.get("konten"))
                        if m.get("file_url"): st.link_button("📎 Buka Modul / Lampiran", m.get("file_url"))
                        
                        col_e, col_d = st.columns(2)
                        with col_e:
                            with st.popover("✏️ Edit Inisiasi"):
                                with st.form(f"f_edit_mat_{m_id}"):
                                    e_sesi = st.selectbox("Sesi", list(range(1, 9)), index=int(sesi_num)-1)
                                    e_judul = st.text_input("Judul Inisiasi", value=m.get("judul",""))
                                    e_konten = st.text_area("Penjelasan Inisiasi", value=m.get("konten",""))
                                    e_url = st.text_input("Link Modul/OER", value=m.get("file_url","") or "")
                                    if st.form_submit_button("Simpan"):
                                        db.collection("materi_pancasila").document(m_id).update({
                                            "sesi": e_sesi, "judul": e_judul, "konten": e_konten, "file_url": e_url.strip() or None, "updated_at": firestore.SERVER_TIMESTAMP
                                        })
                                        clear_materi_cache(); st.success("Diperbarui!"); st.rerun()
                        with col_d:
                            if st.button("🗑️ Hapus", key=f"del_m_{m_id}", type="primary"):
                                db.collection("materi_pancasila").document(m_id).delete()
                                clear_materi_cache(); st.success("Dihapus!"); st.rerun()

        with t_buat:
            with st.form("form_add_inisiasi", clear_on_submit=True):
                sesi_in = st.selectbox("Pilih Sesi Tutorial", list(range(1, 9)))
                judul_in = st.text_input("Judul Materi Inisiasi")
                target_k = st.multiselect("Target Kelas", options=pilihan_kelas, default=pilihan_kelas)
                konten_in = st.text_area("Deskripsi Materi / Pokok Bahasan")
                file_url_in = st.text_input("🔗 Link Modul OER / PPT (Opsional)")
                if st.form_submit_button("📁 Publikasikan Inisiasi"):
                    if judul_in and target_k:
                        db.collection("materi_pancasila").add({
                            "sesi": sesi_in, "bab": f"Sesi {sesi_in}", "judul": judul_in, "target_kelas": target_k,
                            "konten": konten_in, "file_url": file_url_in.strip() or None, "created_at": firestore.SERVER_TIMESTAMP
                        })
                        clear_materi_cache(); st.success("Materi Inisiasi Berhasil Ditambahkan!"); st.rerun()

    elif menu == "💬 Forum Diskusi":
        st.header("💬 Pengelolaan & Penilaian Forum Diskusi")
        col_k, col_s = st.columns(2)
        with col_k: sel_k = st.selectbox("🏫 Pilih Kelas Tuton", options=pilihan_kelas)
        with col_s: sel_sesi = st.selectbox("📌 Pilih Sesi Tutorial", list(range(1, 9)))

        st.subheader(f"Daftar Tanggapan Diskusi Sesi {sel_sesi} ({sel_k})")
        discussions = get_diskusi_by_sesi_kelas(sel_sesi, sel_k)
        
        if not discussions:
            st.info(f"Belum ada tanggapan diskusi dari mahasiswa pada Sesi {sel_sesi} di kelas {sel_k}.")
        else:
            for d in discussions:
                d_id = d["id"]
                with st.expander(f"👤 {d.get('nama_siswa')} (@{d.get('username')}) — Nilai: {d.get('nilai', 'Belum Dinilai')}"):
                    st.write(f"**Tanggapan Mahasiswa:**")
                    st.info(d.get("tanggapan", "(Kosong)"))
                    
                    with st.form(f"f_grade_diskusi_{d_id}"):
                        score_in = st.number_input("Nilai Diskusi (0-100)", 0, 100, value=int(d.get("nilai", 80)) if d.get("nilai") is not None else 80, key=f"score_d_{d_id}")
                        fb_in = st.text_area("Catatan Tutor", value=d.get("catatan_tutor", ""), key=f"fb_d_{d_id}")
                        if st.form_submit_button("💾 Simpan Nilai Diskusi"):
                            db.collection("diskusi_pancasila").document(d_id).update({
                                "nilai": score_in, "catatan_tutor": fb_in, "updated_at": firestore.SERVER_TIMESTAMP
                            })
                            clear_diskusi_cache(); st.success("Nilai Diskusi Berhasil Disimpan!"); st.rerun()

    elif menu == "📝 Tugas Tuton":
        st.header("📝 Kelola Tugas Tuton (Tugas 1, 2, & 3)")
        st.caption("ℹ️ Sesuai standar UT, Tugas Tuton resmi diberikan pada **Sesi 3 (Tugas 1)**, **Sesi 5 (Tugas 2)**, dan **Sesi 7 (Tugas 3)**.")
        
        t_list, t_buat = st.tabs(["📋 Daftar Tugas", "➕ Buat Tugas Sesi"])
        with t_list:
            tugas_cached = get_all_tugas_cached(limit=50)
            if not tugas_cached: st.info("Belum ada tugas dibuat.")
            else:
                for tg in tugas_cached:
                    sesi_str = f"Sesi {tg.get('sesi', 3)}"
                    with st.expander(f"[{sesi_str}] [{tg.get('tipe','').upper()}] {tg.get('judul')}"):
                        st.write(f"**Instruksi:** {tg.get('instruksi')}")
                        st.write(f"**Jumlah Soal:** {len(tg.get('soal', []))}")
                        if st.button("🗑️ Hapus Tugas", key=f"del_tg_{tg['id']}", type="primary"):
                            delete_tugas_and_submissions(tg["id"])
                            st.success("Tugas dihapus!"); st.rerun()

        with t_buat:
            sesi_tg = st.selectbox("Pilih Sesi Tugas", [3, 5, 7], format_func=lambda x: f"Sesi {x} (Tugas {(x//2)})")
            judul_tg = st.text_input("Judul Tugas")
            instruksi_tg = st.text_area("Instruksi / Pentunjuk Pengerjaan")
            target_k = st.multiselect("Target Kelas", options=pilihan_kelas, default=pilihan_kelas)
            tipe_tg = st.radio("Tipe Soal", ["Essay", "Pilihan Ganda"])

            if tipe_tg == "Essay":
                n_essay = st.number_input("Jumlah Soal Essay", 1, 10, 2)
                with st.form("form_create_essay_tuton"):
                    soal_list = [{"pertanyaan": st.text_area(f"Soal #{i+1}", key=f"q_e_{i}")} for i in range(n_essay)]
                    if st.form_submit_button("Simpan Tugas Essay"):
                        if judul_tg and target_k:
                            db.collection("tugas_pancasila").add({
                                "sesi": sesi_tg, "judul": judul_tg, "instruksi": instruksi_tg, "tipe": "essay",
                                "target_kelas": target_k, "status": "terbit", "jenis_tugas": f"Tugas {sesi_tg//2}",
                                "soal": soal_list, "created_at": firestore.SERVER_TIMESTAMP
                            })
                            clear_tugas_cache(); st.success("Tugas Essay Berhasil Diterbitkan!"); st.rerun()

    elif menu == "📊 Rekapitulasi & Nilai UT":
        st.header("📊 Transkrip & Rekapitulasi Nilai Tuton UT")
        if not pilihan_kelas: st.warning("Anda belum ditugaskan mengajar kelas manapun."); st.stop()
        sel_k = st.selectbox("🏫 Pilih Kelas Tuton", options=pilihan_kelas)
        
        siswa_list = get_siswa_by_kelas_cached(sel_k, limit=150)
        tugas_list = [t for t in get_all_tugas_cached(limit=50) if is_target_sesuai_kelas(t, sel_k)]
        pengerjaan_all = get_all_pengerjaan_by_kelas_cached(sel_k, limit=300)
        p_map = {(p.get("username_siswa"), p.get("id_tugas")): p for p in pengerjaan_all}

        rekap_tuton = []
        for s in siswa_list:
            un = s["username"]
            nm = s.get("nama", un)
            
            # 1. Hitung Nilai Kehadiran (Bobot 20%)
            user_hadir = get_kehadiran_user(un)
            cnt_hadir = sum(1 for sesi in range(1, 9) if user_hadir.get(sesi, {}).get("hadir"))
            score_hadir = round((cnt_hadir / 8.0) * 100)

            # 2. Hitung Nilai Rata-rata Diskusi (Bobot 30%)
            diskusi_scores = []
            for sesi in range(1, 9):
                d_docs = get_diskusi_by_sesi_kelas(sesi, sel_k)
                for d in d_docs:
                    if d.get("username") == un and d.get("nilai") is not None:
                        diskusi_scores.append(float(d.get("nilai")))
            score_diskusi = round(sum(diskusi_scores) / len(diskusi_scores), 1) if diskusi_scores else 0.0

            # 3. Hitung Nilai Rata-rata Tugas (Bobot 50%)
            tugas_scores = []
            for tg in tugas_list:
                p = p_map.get((un, tg["id"]), {})
                if p.get("status") == "submitted" and p.get("nilai") is not None:
                    tugas_scores.append(float(p.get("nilai")))
            score_tugas = round(sum(tugas_scores) / len(tugas_scores), 1) if tugas_scores else 0.0

            # Formula Bobot Resmi Tuton UT: (20% Kehadiran) + (30% Diskusi) + (50% Tugas)
            score_akhir_tuton = round((0.20 * score_hadir) + (0.30 * score_diskusi) + (0.50 * score_tugas), 2)

            rekap_tuton.append({
                "NIM": un, "Nama Mahasiswa": nm,
                "Kehadiran (20%)": f"{score_hadir} ({cnt_hadir}/8 Sesi)",
                "Rata Diskusi (30%)": score_diskusi,
                "Rata Tugas (50%)": score_tugas,
                "Nilai Akhir Tuton": score_akhir_tuton
            })

        df_tuton = pd.DataFrame(rekap_tuton)
        st.dataframe(df_tuton, use_container_width=True)
        csv_data = df_tuton.to_csv(index=False).encode('utf-8-sig')
        st.download_button("💾 Unduh Rekap Nilai Tuton (.csv)", csv_data, f"rekap_tuton_ut_{sel_k}.csv", "text/csv", use_container_width=True)

# ==========================================
# 10. PANEL MAHASISWA (SISWA)
# ==========================================
def render_siswa():
    kelas_s = user_info.get("kelas", "-")
    nama_s = user_info.get("nama", "Mahasiswa")
    username_s = user_info.get("username", "")

    my_subs = get_user_pengerjaan_cached(username_s)
    active_quiz_id = st.session_state.get("active_quiz_id")

    # --- JIKA MAHASISWA SEDANG MENGERJAKAN TUGAS ---
    if active_quiz_id:
        tg = next((t for t in get_all_tugas_cached(limit=50) if t["id"] == active_quiz_id), None)
        if not tg: st.session_state["active_quiz_id"] = None; st.rerun()

        tg_id = tg["id"]
        soal_list = tg.get("soal", [])
        total_soal = len(soal_list)
        doc_ref = db.collection("pengerjaan_siswa").document(f"{username_s}_{tg_id}")

        if f"quiz_loaded_{tg_id}" not in st.session_state:
            doc_snap = doc_ref.get()
            ex_data = doc_snap.to_dict() if doc_snap.exists else {}
            st.session_state[f"quiz_answers_{tg_id}"] = ex_data.get("jawaban") if isinstance(ex_data.get("jawaban"), list) and len(ex_data.get("jawaban"))==total_soal else [None]*total_soal
            st.session_state[f"quiz_page_{tg_id}"] = 0
            st.session_state[f"quiz_loaded_{tg_id}"] = True

        answers = st.session_state[f"quiz_answers_{tg_id}"]
        curr_page = st.session_state[f"quiz_page_{tg_id}"]

        st.markdown(f"### 📝 {tg.get('judul')} (Sesi {tg.get('sesi', 3)})")
        st.progress((curr_page + 1) / max(1, total_soal))
        
        if 0 <= curr_page < total_soal:
            sq = soal_list[curr_page]
            q_text = sq.get("pertanyaan", "") if isinstance(sq, dict) else str(sq)
            st.markdown(f"#### Soal #{curr_page + 1}: {q_text}")

            if tg.get("tipe") == "pg":
                opsi = sq.get("opsi", ["", "", "", ""])
                curr_a = answers[curr_page]
                curr_idx = curr_a if isinstance(curr_a, int) and 0 <= curr_a <= 3 else None
                sel_o = st.radio("Pilih Jawaban:", opsi, index=curr_idx, key=f"rad_{tg_id}_{curr_page}")
                if sel_o in opsi:
                    new_idx = opsi.index(sel_o)
                    if answers[curr_page] != new_idx:
                        answers[curr_page] = new_idx
                        st.session_state[f"quiz_answers_{tg_id}"] = answers
                        doc_ref.set({"jawaban": answers, "updated_at": firestore.SERVER_TIMESTAMP}, merge=True)
            else:
                curr_essay = answers[curr_page] or ""
                val_e = st.text_area("Jawaban Anda:", value=curr_essay, key=f"txt_{tg_id}_{curr_page}")
                if answers[curr_page] != val_e:
                    answers[curr_page] = val_e
                    st.session_state[f"quiz_answers_{tg_id}"] = answers
                    doc_ref.set({"jawaban": answers, "updated_at": firestore.SERVER_TIMESTAMP}, merge=True)

        st.divider()
        c1, c2, c3 = st.columns([1, 1, 1])
        with c1:
            if curr_page > 0 and st.button("⬅️ Sebelumnya"):
                st.session_state[f"quiz_page_{tg_id}"] -= 1; st.rerun()
        with c2:
            if curr_page < total_soal - 1 and st.button("Berikutnya ➡️"):
                st.session_state[f"quiz_page_{tg_id}"] += 1; st.rerun()
        with c3:
            if curr_page == total_soal - 1 and st.button("🚀 Kirim Jawaban Tugas", type="primary"):
                submit_jawaban_siswa(tg, username_s, nama_s, kelas_s, answers)
                st.session_state["active_quiz_id"] = None
                st.success("Tugas Berhasil Dikirim!"); st.rerun()
        st.stop()

    # --- DASHBOARD UTAMA TUTON MAHASISWA ---
    st.markdown(f"""
        <div class="ut-header">
            <h2 style="margin:0; color:#FFC72C;">🎓 TUTORIAL ONLINE UNIVERSITAS TERBUKA</h2>
            <p style="margin:0; font-size:15px;"><b>Pendidikan Pancasila (MKDU4111)</b> | Kelas: <b>{kelas_s}</b></p>
            <p style="margin:0; font-size:13px; opacity:0.8;">Mahasiswa: {nama_s} (NIM: @{username_s})</p>
        </div>
    """, unsafe_allow_html=True)

    # Dapatkan Data Kehadiran Mahasiswa
    kehadiran_dict = get_kehadiran_user(username_s)

    # NAVIGASI SESI 1 - 8 TUTON UT
    tabs_sesi = st.tabs([f"Sesi {i}" for i in range(1, 9)])

    materi_docs = get_all_materi_cached(limit=50)
    tugas_docs = get_all_tugas_cached(limit=50)

    for i, tab in enumerate(tabs_sesi, 1):
        with tab:
            st.markdown(f"### 📌 Ruang Tuton - Sesi Tutorial {i}")
            
            # --- 1. FITUR KEHADIRAN SESI ---
            st.markdown("#### 1. Kehadiran Sesi")
            is_hadir = kehadiran_dict.get(i, {}).get("hadir", False)
            if is_hadir:
                st.success("✅ Anda telah mengisi konfirmasi kehadiran pada Sesi ini.")
            else:
                if st.button(f"✋ Konfirmasi Kehadiran Sesi {i}", key=f"btn_hadir_{i}", type="primary"):
                    db.collection("kehadiran_siswa").document(f"{username_s}_sesi_{i}").set({
                        "username": username_s, "nama": nama_s, "kelas": kelas_s,
                        "sesi": i, "hadir": True, "timestamp": firestore.SERVER_TIMESTAMP
                    })
                    clear_kehadiran_cache(); st.success(f"Kehadiran Sesi {i} Berhasil Dicatat!"); st.rerun()
            
            st.divider()

            # --- 2. MATERI INISIASI ---
            st.markdown("#### 2. Materi Inisiasi")
            m_sesi = [m for m in materi_docs if m.get("sesi") == i or m.get("bab") == f"Sesi {i}"]
            if not m_sesi:
                st.info(f"Belum ada materi inisiasi pada Sesi {i}.")
            else:
                for m in m_sesi:
                    with st.container(border=True):
                        st.markdown(f"##### 📘 {m.get('judul')}")
                        if m.get("konten"): st.write(m.get("konten"))
                        if m.get("file_url"): st.link_button("📎 Buka Modul / Bahan Ajar", m.get("file_url"))

            st.divider()

            # --- 3. FORUM DISKUSI SESI ---
            st.markdown("#### 3. Forum Diskusi")
            st.caption("Silakan berikan tanggapan atau tanggapi topik diskusi yang diberikan oleh Tutor.")
            
            d_docs = get_diskusi_by_sesi_kelas(i, kelas_s)
            my_d = next((d for d in d_docs if d.get("username") == username_s), None)

            if my_d:
                st.success("✅ Anda telah mengirimkan tanggapan diskusi pada Sesi ini.")
                st.markdown(f"**Tanggapan Anda:**\n\n_{my_d.get('tanggapan')}_")
                if my_d.get("nilai") is not None:
                    st.info(f"📊 Nilai Diskusi Tutor: **{my_d.get('nilai')}** / 100\n\nCatatan Tutor: {my_d.get('catatan_tutor', '-')}")
            else:
                with st.form(f"f_diskusi_mhs_{i}"):
                    resp_diskusi = st.text_area(f"Tuliskan Tanggapan Diskusi Sesi {i} Anda di sini:")
                    if st.form_submit_button("🚀 Kirim Tanggapan Diskusi"):
                        if resp_diskusi.strip():
                            db.collection("diskusi_pancasila").add({
                                "sesi": i, "kelas": kelas_s, "username": username_s, "nama_siswa": nama_s,
                                "tanggapan": resp_diskusi.strip(), "nilai": None, "catatan_tutor": "", "created_at": firestore.SERVER_TIMESTAMP
                            })
                            clear_diskusi_cache(); st.success("Tanggapan Diskusi Berhasil Dikirim!"); st.rerun()
                        else: st.warning("Isi tanggapan diskusi terlebih dahulu.")

            st.divider()

            # --- 4. TUGAS TUTON (SESI 3, 5, 7) ---
            if i in [3, 5, 7]:
                st.markdown(f"#### 4. Tugas Tuton {i//2}")
                t_sesi = [t for t in tugas_docs if t.get("sesi") == i and is_target_sesuai_kelas(t, kelas_s)]
                if not t_sesi:
                    st.info(f"Belum ada Tugas Tuton yang dikonfigurasi pada Sesi {i}.")
                else:
                    for tg in t_sesi:
                        tg_id = tg["id"]
                        sub = my_subs.get(tg_id, {})
                        status_sub = sub.get("status", "belum")

                        with st.container(border=True):
                            st.markdown(f"##### 📝 {tg.get('judul')}")
                            if tg.get("instruksi"): st.write(tg.get("instruksi"))
                            
                            if status_sub == "submitted":
                                val_n = sub.get("nilai")
                                st.success(f"✅ Sudah Dikerjakan | Nilai: **{val_n if val_n is not None else 'Sedang Dinilai Tutor'}**")
                            else:
                                if st.button(f"Mulai Kerjakan Tugas Tuton {i//2} 🚀", key=f"btn_tg_s_{tg_id}", type="primary"):
                                    st.session_state["active_quiz_id"] = tg_id
                                    st.rerun()

# ==========================================
# 11. MAIN APP ROUTER
# ==========================================
if role == "superadmin":
    render_superadmin()
elif role == "guru":
    render_guru()
elif role == "siswa":
    render_siswa()
else:
    st.error("Role pengguna tidak dikenal.")
