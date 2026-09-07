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

try:
    from google.cloud.firestore_bundle import FirestoreBundle
except ImportError:
    try:
        from google.cloud.firestore_v1.bundle import FirestoreBundle
    except ImportError:
        FirestoreBundle = None

# ==========================================
# 1. CONFIG & BRANDING STYLING SMP
# ==========================================
st.set_page_config(
    page_title="LMS SMP - Pendidikan Pancasila",
    page_icon="🏫",
    layout="wide",
    initial_sidebar_state="collapsed"
)

st.markdown("""
    <style>
    html, body, [class*="css"] {
        font-family: -apple-system, BlinkMacSystemFont, "Segoe UI", Roboto, Helvetica, Arial, sans-serif;
    }
    :root {
        --smp-blue: #1E3A8A;
        --smp-sky: #0284C7;
        --smp-light-bg: #F0F9FF;
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
    .smp-header {
        background: linear-gradient(135deg, #1E3A8A 0%, #0284C7 100%);
        border-bottom: 5px solid #38BDF8;
        color: white; padding: 20px 24px; border-radius: 12px; margin-bottom: 20px;
        box-shadow: 0 4px 12px rgba(0,0,0,0.1);
    }
    .smp-card {
        background: white; border: 1px solid #e2e8f0; border-radius: 10px; padding: 16px; margin-bottom: 12px;
        border-left: 4px solid #1E3A8A;
    }
    .stTabs [data-baseweb="tab-list"] { gap: 8px; overflow-x: auto; white-space: nowrap; border-bottom: 2px solid #eaeaea; padding-bottom: 4px; }
    .stTabs [data-baseweb="tab"] { padding: 8px 16px; border-radius: 16px; font-weight: 600; font-size: 14px; }
    .stTabs [aria-selected="true"] { background-color: #1E3A8A !important; color: #FFFFFF !important; }
    
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

# --- FIRESTORE AGGREGATION QUERIES ---
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
            "nama": "Administrator Sekolah",
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
    return sorted(doc.to_dict().get("daftar", [])) if doc.exists else ["7-A", "7-B", "8-A", "8-B", "9-A"]

@st.cache_data(ttl=86400)
def get_all_bab_data():
    """Mengambil seluruh data BAB beserta target kelas & status aktifnya."""
    doc = db.collection("config").document("master_bab").get()
    if doc.exists:
        data = doc.to_dict()
        if "items" in data:
            items = data["items"]
            for b in items:
                if "is_active" not in b:
                    b["is_active"] = True
            return items
        if "daftar" in data:
            all_k = get_all_kelas()
            items = [{"nama": b, "target_kelas": all_k, "is_active": True} for b in data["daftar"]]
            db.collection("config").document("master_bab").set({"items": items}, merge=True)
            return items
            
    all_k = get_all_kelas()
    default_items = [
        {"nama": "BAB 1: Kedudukan dan Fungsi Pancasila", "target_kelas": all_k, "is_active": True},
        {"nama": "BAB 2: Bentuk dan Kedaulatan Negara", "target_kelas": all_k, "is_active": True}
    ]
    db.collection("config").document("master_bab").set({"items": default_items}, merge=True)
    return default_items

def get_bab_for_kelas(kelas_siswa=None, only_active=False):
    """Mengambil daftar nama BAB yang ditargetkan untuk kelas tertentu (opsional filter aktif)."""
    items = get_all_bab_data()
    daftar_nama = []
    for b in items:
        if only_active and not b.get("is_active", True):
            continue
        target = b.get("target_kelas", [])
        if not kelas_siswa or not target or kelas_siswa in target:
            daftar_nama.append(b["nama"])
    return daftar_nama

@st.cache_data(ttl=60)
def get_diskusi_status_cached():
    """Mengambil status aktif/nonaktif forum diskusi."""
    doc = db.collection("config").document("master_diskusi_status").get()
    return doc.to_dict() if doc.exists else {}

def is_diskusi_aktif(bab, pertemuan, kelas=None):
    """Cek apakah diskusi tertentu aktif."""
    status_map = get_diskusi_status_cached()
    key_specific = f"{bab}_p{pertemuan}_{kelas}"
    key_global = f"{bab}_p{pertemuan}"
    if key_specific in status_map:
        return status_map[key_specific]
    return status_map.get(key_global, True)

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
def get_all_pengerjaan_by_kelas_cached(kelas, limit=300):
    docs = db.collection("pengerjaan_siswa").where("kelas_siswa", "==", kelas).limit(limit).stream()
    return [d.to_dict() for d in docs]

@st.cache_data(ttl=60)
def get_diskusi_by_bab_pertemuan_kelas(bab, pertemuan, kelas):
    docs = db.collection("diskusi_pancasila").where("bab", "==", bab).where("pertemuan", "==", int(pertemuan)).where("kelas", "==", kelas).stream()
    return [{"id": d.id, **d.to_dict()} for d in docs]

@st.cache_data(ttl=60)
def get_kehadiran_user(username):
    docs = db.collection("kehadiran_siswa").where("username", "==", username).stream()
    return {f"{d.to_dict().get('bab')}_p{d.to_dict().get('pertemuan')}": d.to_dict() for d in docs}

# --- CACHE CLEAR HELPERS ---
def clear_kelas_cache(): 
    get_all_kelas.clear()
    get_guru_dashboard_stats.clear()

def clear_bab_cache():
    get_all_bab_data.clear()

def clear_diskusi_status_cache():
    get_diskusi_status_cached.clear()

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
    get_all_pengerjaan_by_kelas_cached.clear()
    count_submitted_by_tugas_kelas.clear()
    get_guru_dashboard_stats.clear()

def clear_diskusi_cache():
    get_diskusi_by_bab_pertemuan_kelas.clear()

def clear_kehadiran_cache():
    get_kehadiran_user.clear()

# ==========================================
# 3. UTILITIES
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
    first_name = nama.strip().split()[0] if nama.strip() else "siswa"
    base_username = re.sub(r'[^a-z0-9]', '', first_name.lower())[:5] or "siswa"
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

def submit_jawaban_siswa(tg, username_s, nama_s, kelas_s, answers):
    tg_id = tg["id"]
    soal_list = tg.get("soal", [])
    total_soal = len(soal_list)
    catatan = "Penilaian Otomatis Sistem"
    doc_ref = db.collection("pengerjaan_siswa").document(f"{username_s}_{tg_id}")

    if tg.get("tipe") == "pg":
        correct_count = sum(1 for idx_q, sq in enumerate(soal_list) if idx_q < len(answers) and answers[idx_q] == sq.get("kunci"))
        score = round((correct_count / total_soal) * 100) if total_soal > 0 else 0
        doc_ref.set({
            "id_tugas": tg_id, "judul_tugas": tg.get("judul"), "bab": tg.get("bab"), "pertemuan": tg.get("pertemuan"),
            "username_siswa": username_s, "nama_siswa": nama_s, "kelas_siswa": kelas_s, "tipe": "pg", "jawaban": answers,
            "nilai": score, "catatan_guru": catatan, "status": "submitted", "submitted_at": firestore.SERVER_TIMESTAMP, "updated_at": firestore.SERVER_TIMESTAMP
        }, merge=True)
    else:
        doc_ref.set({
            "id_tugas": tg_id, "judul_tugas": tg.get("judul"), "bab": tg.get("bab"), "pertemuan": tg.get("pertemuan"),
            "username_siswa": username_s, "nama_siswa": nama_s, "kelas_siswa": kelas_s, "tipe": "essay", "soal": soal_list,
            "jawaban": answers, "nilai": None, "catatan_guru": catatan, "status": "submitted", "submitted_at": firestore.SERVER_TIMESTAMP, "updated_at": firestore.SERVER_TIMESTAMP
        }, merge=True)
    clear_pengerjaan_cache()
    return True

# ==========================================
# 4. AUTHENTICATION
# ==========================================
if "user" not in st.session_state:
    st.session_state["user"] = None

ensure_default_admin_created()

if st.session_state["user"] is None:
    st.markdown("""
        <div style="text-align: center; padding: 20px;">
            <h1 style="color: #1E3A8A; margin-bottom: 0;">🏫 LMS PENDIDIKAN PANCASILA SMP</h1>
            <p style="color: #666; font-weight: 600;">Sistem Pembelajaran Digital Tingkat SMP</p>
        </div>
    """, unsafe_allow_html=True)
    
    with st.form("form_login"):
        username = st.text_input("NIS / Username").strip().lower()
        password = st.text_input("Password", type="password")
        if st.form_submit_button("Masuk Ke LMS"):
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
                else: st.error("NIS/Username atau password salah!")
            else: st.warning("Silakan lengkapi NIS/Username & Password.")
    st.stop()

# ==========================================
# 5. SIDEBAR
# ==========================================
user_info = st.session_state["user"]
role = user_info["role"]
role_label = "Guru" if role == "guru" else ("Siswa" if role == "siswa" else "Super Admin")

st.sidebar.markdown(f"### 🏫 LMS Pancasila SMP")
st.sidebar.caption(f"👤 **{user_info['nama']}**\n\nPeran: **{role_label}** | @{user_info['username']}")
if role == "siswa" and user_info.get("kelas"):
    st.sidebar.caption(f"🏫 Kelas: **{user_info['kelas']}**")

if st.sidebar.button("🚪 Keluar / Logout"):
    st.session_state.clear()
    components.html("<script>sessionStorage.clear(); localStorage.clear();</script>", height=0)
    st.rerun()

st.sidebar.divider()

# ==========================================
# 6. PANEL SUPER ADMIN
# ==========================================
def render_superadmin():
    st.title("⚙️ Panel Administrator LMS SMP")
    t_kelas, t_list, t_add, t_imp, t_edit, t_del = st.tabs([
        "🏫 Kelola Kelas", "👥 Daftar User", "➕ Buat Akun", "📥 Import/Export", "✏️ Atur Kelas", "🗑️ Hapus Akun"
    ])

    with t_kelas:
        st.subheader("🏫 Kelola Kelas SMP")
        daftar_kelas = get_all_kelas()
        col1, col2 = st.columns(2)
        with col1:
            for k in daftar_kelas: st.markdown(f"- 🏫 Kelas: **{k}**")
        with col2:
            with st.form("f_add_k", clear_on_submit=True):
                new_k = st.text_input("Nama Kelas Baru (cth: 7-C)").strip()
                if st.form_submit_button("Tambah Kelas"):
                    if new_k and new_k not in daftar_kelas:
                        db.collection("config").document("master_kelas").set({"daftar": sorted(daftar_kelas + [new_k])}, merge=True)
                        clear_kelas_cache()
                        st.success(f"Kelas {new_k} berhasil ditambahkan.")
                        st.rerun()

    with t_list:
        st.subheader("👥 Daftar Pengguna Sistem")
        role_filter = st.selectbox("Filter Peran", ["semua", "siswa", "guru", "superadmin"], format_func=lambda x: "Siswa" if x=="siswa" else ("Guru" if x=="guru" else x.upper()))
        total_users = count_all_users(role_filter)
        curr_page, limit, offset = render_pagination_controls(total_users, default_page_size=10, key_prefix="u_pg")
        
        paginated = get_users_paginated(limit=limit, offset=offset, role_filter=role_filter)
        u_table = [{"NIS/Username": u.get("id"), "Nama": u.get("nama"), "Peran": "Siswa" if u.get("role")=="siswa" else ("Guru" if u.get("role")=="guru" else "Admin"), "Kelas": u.get("kelas") or ", ".join(u.get("kelas_ajar", []))} for u in paginated]
        if u_table: st.dataframe(pd.DataFrame(u_table), use_container_width=True)

    with t_add:
        st.subheader("➕ Buat Akun Satuan")
        daftar_kelas = get_all_kelas()
        new_role_sel = st.selectbox("Peran User", ["Siswa", "Guru", "Superadmin"])
        new_role = "siswa" if new_role_sel == "Siswa" else ("guru" if new_role_sel == "Guru" else "superadmin")
        
        with st.form("form_add_user", clear_on_submit=True):
            nama = st.text_input("Nama Lengkap")
            uname = st.text_input("NIS / Username").strip().lower()
            pwd = st.text_input("Password", type="password")
            k_s = st.selectbox("Kelas Siswa", options=daftar_kelas) if new_role == "siswa" else None
            k_g = st.multiselect("Kelas Ajar Guru", options=daftar_kelas) if new_role == "guru" else None
            
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
        st.subheader("📥 Import Data Siswa & Guru")
        target_role_imp = st.radio("Peran Import:", ["Siswa", "Guru"], horizontal=True)
        up_file = st.file_uploader("Upload File (.csv / .xlsx)", type=["csv", "xlsx"])
        if up_file and st.button("🚀 Proses Import"):
            df = safe_read_uploaded_file(up_file)
            df.columns = [str(c).strip().lower() for c in df.columns]
            if "nama" in df.columns and "kelas" in df.columns:
                role_str = "siswa" if target_role_imp == "Siswa" else "guru"
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

# ==========================================
# 7. PANEL GURU
# ==========================================
def render_guru():
    st.markdown("""
        <div class="smp-header">
            <h2 style="margin:0; color:#FFFFFF;">🎓 DASHBOARD GURU PENDIDIKAN PANCASILA SMP</h2>
            <p style="margin:0; font-size:14px;">Pengelolaan BAB, Status Aktif Pembelajaran, Forum Diskusi, UTS BAB, dan UAS BAB</p>
        </div>
    """, unsafe_allow_html=True)
    
    pilihan_kelas = user_info.get("kelas_ajar") or get_all_kelas()
    if isinstance(pilihan_kelas, str): pilihan_kelas = [pilihan_kelas]

    guru_stats = get_guru_dashboard_stats(tuple(pilihan_kelas))
    st.markdown("##### ⚡ Ringkasan Statistik Pembelajaran SMP")
    m1, m2, m3, m4 = st.columns(4)
    m1.metric("👥 Total Siswa", f"{guru_stats['total_siswa']}")
    m2.metric("📝 Total Ujian Bab", f"{guru_stats['total_tugas']}")
    m3.metric("📖 Total Materi Bab", f"{guru_stats['total_materi']}")
    m4.metric("💬 Pengumpulan Ujian", f"{guru_stats['total_submitted']}")
    st.divider()

    menu = st.sidebar.radio("📌 Menu Guru", [
        "📚 Kelola BAB & Materi", 
        "💬 Forum Diskusi", 
        "📝 Kelola UTS & UAS BAB", 
        "📊 Rekapitulasi Nilai SMP"
    ])

    daftar_bab_data = get_all_bab_data()
    daftar_bab = [b["nama"] for b in daftar_bab_data]

    if menu == "📚 Kelola BAB & Materi":
        st.header("📚 Kelola BAB & Pertemuan Pembelajaran")
        st.caption("ℹ️ Tentukan BAB mana yang **aktif**, kelola **target kelas**, serta **keaktifan modul materi**.")

        t_materi, t_bab = st.tabs(["📋 Materi Pertemuan", "⚙️ Kelola & Toggle BAB"])

        with t_bab:
            st.subheader("⚙️ Status Aktif & Target Kelas BAB")
            col_b1, col_b2 = st.columns([3, 2])
            
            with col_b1:
                st.markdown("**Daftar BAB & Status Keaktifannya:**")
                bab_updated = False
                for idx, b in enumerate(daftar_bab_data):
                    with st.container(border=True):
                        cb_col1, cb_col2 = st.columns([3, 1])
                        with cb_col1:
                            target_str = ", ".join(b.get("target_kelas", [])) if b.get("target_kelas") else "Semua Kelas"
                            status_badge = "🟢 **AKTIF**" if b.get("is_active", True) else "🔴 **NONAKTIF**"
                            st.markdown(f"##### {b['nama']}\nStatus: {status_badge}  \n📌 *Kelas: {target_str}*")
                        with cb_col2:
                            is_act = st.toggle("Aktifkan", value=b.get("is_active", True), key=f"tg_bab_{idx}")
                            if is_act != b.get("is_active", True):
                                daftar_bab_data[idx]["is_active"] = is_act
                                bab_updated = True
                
                if bab_updated:
                    db.collection("config").document("master_bab").set({"items": daftar_bab_data})
                    clear_bab_cache()
                    st.success("Status keaktifan BAB berhasil diperbarui!")
                    st.rerun()

            with col_b2:
                with st.form("form_add_bab_with_kelas", clear_on_submit=True):
                    st.markdown("**➕ Tambah BAB Baru**")
                    new_bab_title = st.text_input("Nama BAB Baru (Contoh: BAB 3: Tata Urutan Peraturan)").strip()
                    target_k_bab = st.multiselect("Target Kelas untuk BAB Ini", options=get_all_kelas(), default=pilihan_kelas)
                    is_active_new = st.checkbox("Langsung Aktifkan BAB", value=True)
                    
                    if st.form_submit_button("➕ Simpan BAB"):
                        if new_bab_title and target_k_bab:
                            if not any(b["nama"] == new_bab_title for b in daftar_bab_data):
                                daftar_bab_data.append({
                                    "nama": new_bab_title,
                                    "target_kelas": target_k_bab,
                                    "is_active": is_active_new
                                })
                                db.collection("config").document("master_bab").set({"items": daftar_bab_data})
                                clear_bab_cache()
                                st.success(f"'{new_bab_title}' berhasil ditambahkan!")
                                st.rerun()
                            else:
                                st.warning("Nama BAB tersebut sudah ada!")
                        else:
                            st.warning("Mohon isi nama BAB dan pilih minimal satu target kelas.")

                st.divider()

                if daftar_bab_data:
                    with st.expander("✏️ Edit Target Kelas BAB"):
                        selected_edit_bab = st.selectbox("Pilih BAB untuk Diubah Kelasnya", options=daftar_bab, key="sb_edit_bab")
                        bab_obj = next(b for b in daftar_bab_data if b["nama"] == selected_edit_bab)
                        
                        with st.form("form_edit_bab_kelas"):
                            updated_target_k = st.multiselect("Target Kelas Baru", options=get_all_kelas(), default=bab_obj.get("target_kelas", []))
                            if st.form_submit_button("💾 Simpan Perubahan Kelas"):
                                bab_obj["target_kelas"] = updated_target_k
                                db.collection("config").document("master_bab").set({"items": daftar_bab_data})
                                clear_bab_cache()
                                st.success(f"Target kelas untuk '{selected_edit_bab}' berhasil diperbarui!")
                                st.rerun()

                    st.divider()

                    with st.form("form_del_bab"):
                        bab_to_del = st.selectbox("Pilih BAB yang akan Dihapus", options=daftar_bab)
                        if st.form_submit_button("🗑️ Hapus BAB", type="primary"):
                            updated_items = [b for b in daftar_bab_data if b["nama"] != bab_to_del]
                            db.collection("config").document("master_bab").set({"items": updated_items})
                            clear_bab_cache()
                            st.success(f"'{bab_to_del}' berhasil dihapus!")
                            st.rerun()

        with t_materi:
            if not daftar_bab:
                st.info("Belum ada BAB. Silakan buat BAB terlebih dahulu pada tab '⚙️ Kelola & Toggle BAB'.")
            else:
                sel_bab = st.selectbox("Pilih BAB", options=daftar_bab)
                sel_p = st.selectbox("Pilih Pertemuan", [1, 2, 3, 4, 5, 6], format_func=lambda x: f"Pertemuan {x} - {'UTS BAB' if x==3 else ('UAS BAB' if x==6 else 'Diskusi & Materi')}")

                st.subheader(f"Materi Pembelajaran - {sel_bab} (Pertemuan {sel_p})")
                
                materi_cached = get_all_materi_cached(limit=100)
                m_curr = [m for m in materi_cached if m.get("bab") == sel_bab and int(m.get("pertemuan", 1)) == sel_p]

                if m_curr:
                    for m in m_curr:
                        with st.container(border=True):
                            cm1, cm2 = st.columns([3, 1])
                            with cm1:
                                is_m_active = m.get("is_active", True)
                                badge_m = "🟢 Aktif" if is_m_active else "🔴 Nonaktif"
                                st.markdown(f"##### 📘 {m.get('judul')} ({badge_m})")
                                st.write(m.get("konten", ""))
                                if m.get("file_url"): st.link_button("📎 Buka Modul / Lampiran", m.get("file_url"))
                            with cm2:
                                toggle_mat = st.toggle("Aktifkan Materi", value=is_m_active, key=f"tg_materi_{m['id']}")
                                if toggle_mat != is_m_active:
                                    db.collection("materi_pancasila").document(m["id"]).update({"is_active": toggle_mat})
                                    clear_materi_cache()
                                    st.success("Status materi berhasil diperbarui!")
                                    st.rerun()

                                if st.button("🗑️ Hapus Materi", key=f"del_mat_{m['id']}", type="primary"):
                                    db.collection("materi_pancasila").document(m["id"]).delete()
                                    clear_materi_cache(); st.success("Materi dihapus!"); st.rerun()
                else:
                    st.info("Belum ada materi untuk pertemuan ini.")

                with st.expander("➕ Upload / Tambah Materi Pertemuan"):
                    with st.form(f"f_add_materi_{sel_bab}_{sel_p}", clear_on_submit=True):
                        judul_m = st.text_input("Judul Materi Pertemuan")
                        target_k = st.multiselect("Target Kelas", options=pilihan_kelas, default=pilihan_kelas)
                        konten_m = st.text_area("Deskripsi / Ringkasan Materi")
                        file_url_m = st.text_input("🔗 Link Bahan Ajar / Slide PPT / Video (Opsional)")
                        is_active_m = st.checkbox("Publikasikan (Aktifkan untuk Siswa)", value=True)
                        
                        if st.form_submit_button("📁 Simpan Materi"):
                            if judul_m and target_k:
                                db.collection("materi_pancasila").add({
                                    "bab": sel_bab, "pertemuan": sel_p, "judul": judul_m,
                                    "target_kelas": target_k, "konten": konten_m, "file_url": file_url_m.strip() or None,
                                    "is_active": is_active_m, "created_at": firestore.SERVER_TIMESTAMP
                                })
                                clear_materi_cache(); st.success("Materi berhasil disimpan!"); st.rerun()

    elif menu == "💬 Forum Diskusi":
        st.header("💬 Pengelolaan Forum Diskusi")
        st.caption("ℹ️ Guru dapat menentukan apakah **forum diskusi aktif/terbuka** atau **ditutup** untuk siswa.")

        col_b, col_k, col_p = st.columns(3)
        with col_b: sel_bab = st.selectbox("Pilih BAB", options=daftar_bab)
        with col_k: sel_k = st.selectbox("Pilih Kelas SMP", options=pilihan_kelas)
        with col_p: sel_p = st.selectbox("Pilih Pertemuan Diskusi", [1, 2, 4, 5], format_func=lambda x: f"Pertemuan {x} (Diskusi)")

        # Control active status of Discussion
        curr_diskusi_status = is_diskusi_aktif(sel_bab, sel_p, sel_k)
        with st.container(border=True):
            cd1, cd2 = st.columns([3, 1])
            with cd1:
                st.markdown(f"##### ⚙️ Status Forum Diskusi: {'🟢 **DIBUKA / AKTIF**' if curr_diskusi_status else '🔴 **DITUTUP / NONAKTIF**'}")
                st.caption(f"BAB: {sel_bab} | Pertemuan {sel_p} | Kelas {sel_k}")
            with cd2:
                toggle_diskusi = st.toggle("Buka Forum Diskusi", value=curr_diskusi_status, key=f"tg_disk_{sel_bab}_{sel_p}_{sel_k}")
                if toggle_diskusi != curr_diskusi_status:
                    db.collection("config").document("master_diskusi_status").set({
                        f"{sel_bab}_p{sel_p}_{sel_k}": toggle_diskusi
                    }, merge=True)
                    clear_diskusi_status_cache()
                    st.success("Status Forum Diskusi berhasil diperbarui!")
                    st.rerun()

        st.subheader(f"Tanggapan Siswa - {sel_bab} | Pertemuan {sel_p} ({sel_k})")
        discussions = get_diskusi_by_bab_pertemuan_kelas(sel_bab, sel_p, sel_k)

        if not discussions:
            st.info(f"Belum ada tanggapan diskusi dari siswa pada Pertemuan {sel_p} kelas {sel_k}.")
        else:
            for d in discussions:
                d_id = d["id"]
                with st.expander(f"👤 {d.get('nama_siswa')} (@{d.get('username')}) — Nilai: {d.get('nilai', 'Belum Dinilai')}"):
                    st.write("**Tanggapan Siswa:**")
                    st.info(d.get("tanggapan", "(Kosong)"))
                    
                    with st.form(f"f_grade_diskusi_{d_id}"):
                        score_in = st.number_input("Nilai Diskusi (0-100)", 0, 100, value=int(d.get("nilai", 80)) if d.get("nilai") is not None else 80, key=f"score_d_{d_id}")
                        fb_in = st.text_area("Catatan Guru", value=d.get("catatan_guru", ""), key=f"fb_d_{d_id}")
                        if st.form_submit_button("💾 Simpan Nilai Diskusi"):
                            db.collection("diskusi_pancasila").document(d_id).update({
                                "nilai": score_in, "catatan_guru": fb_in, "updated_at": firestore.SERVER_TIMESTAMP
                            })
                            clear_diskusi_cache(); st.success("Nilai Diskusi Berhasil Disimpan!"); st.rerun()

    elif menu == "📝 Kelola UTS & UAS BAB":
        st.header("📝 Kelola Evaluasi BAB (UTS & UAS BAB)")
        st.caption("ℹ️ **Pertemuan 3** digunakan untuk **UTS BAB**, dan **Pertemuan 6** digunakan untuk **UAS BAB**.")

        t_list, t_buat = st.tabs(["📋 Daftar Ujian BAB", "➕ Buat Soal UTS / UAS BAB"])

        with t_list:
            tugas_cached = get_all_tugas_cached(limit=50)
            if not tugas_cached: st.info("Belum ada soal ujian BAB dibuat.")
            else:
                for tg in tugas_cached:
                    jenis_str = tg.get("jenis_tugas", "Evaluasi BAB")
                    with st.expander(f"[{tg.get('bab')}] [{jenis_str}] - Pertemuan {tg.get('pertemuan')} ({tg.get('tipe','').upper()})"):
                        st.write(f"**Judul:** {tg.get('judul')}")
                        st.write(f"**Instruksi:** {tg.get('instruksi')}")
                        st.write(f"**Jumlah Soal:** {len(tg.get('soal', []))}")
                        if st.button("🗑️ Hapus Soal Ujian", key=f"del_tg_{tg['id']}", type="primary"):
                            db.collection("tugas_pancasila").document(tg["id"]).delete()
                            clear_tugas_cache(); st.success("Ujian dihapus!"); st.rerun()

        with t_buat:
            sel_bab = st.selectbox("Pilih BAB Ujian", options=daftar_bab, key="sel_bab_ujian")
            tipe_ujian = st.radio("Pilih Jenis Ujian BAB", ["UTS BAB (Pertemuan 3)", "UAS BAB (Pertemuan 6)"])
            p_num = 3 if "UTS" in tipe_ujian else 6
            jenis_str = "UTS BAB" if p_num == 3 else "UAS BAB"

            judul_tg = st.text_input("Judul Ujian BAB", value=f"{jenis_str} - {sel_bab}")
            instruksi_tg = st.text_area("Instruksi / Petunjuk Pengerjaan")
            target_k = st.multiselect("Target Kelas", options=pilihan_kelas, default=pilihan_kelas)
            tipe_soal = st.radio("Tipe Soal", ["Pilihan Ganda", "Essay"])

            if tipe_soal == "Essay":
                n_essay = st.number_input("Jumlah Soal Essay", 1, 10, 3)
                with st.form("form_create_essay_smp"):
                    soal_list = [{"pertanyaan": st.text_area(f"Soal #{i+1}", key=f"q_e_{i}")} for i in range(n_essay)]
                    if st.form_submit_button("Publish Ujian Essay"):
                        if judul_tg and target_k:
                            db.collection("tugas_pancasila").add({
                                "bab": sel_bab, "pertemuan": p_num, "jenis_tugas": jenis_str,
                                "judul": judul_tg, "instruksi": instruksi_tg, "tipe": "essay",
                                "target_kelas": target_k, "soal": soal_list, "created_at": firestore.SERVER_TIMESTAMP
                            })
                            clear_tugas_cache(); st.success(f"{jenis_str} berhasil diterbitkan!"); st.rerun()
            else:
                n_pg = st.number_input("Jumlah Soal Pilihan Ganda", 1, 20, 5)
                with st.form("form_create_pg_smp"):
                    soal_pg_list = []
                    for i in range(n_pg):
                        st.markdown(f"**Soal PG #{i+1}**")
                        p_txt = st.text_area(f"Pertanyaan #{i+1}", key=f"pg_q_{i}")
                        o_a = st.text_input(f"Opsi A #{i+1}", key=f"pg_oa_{i}")
                        o_b = st.text_input(f"Opsi B #{i+1}", key=f"pg_ob_{i}")
                        o_c = st.text_input(f"Opsi C #{i+1}", key=f"pg_oc_{i}")
                        o_d = st.text_input(f"Opsi D #{i+1}", key=f"pg_od_{i}")
                        kunci_idx = st.selectbox(f"Kunci Jawaban #{i+1}", [0, 1, 2, 3], format_func=lambda x: ["A", "B", "C", "D"][x], key=f"pg_k_{i}")
                        soal_pg_list.append({"pertanyaan": p_txt, "opsi": [o_a, o_b, o_c, o_d], "kunci": kunci_idx})
                    
                    if st.form_submit_button("Publish Ujian PG"):
                        if judul_tg and target_k:
                            db.collection("tugas_pancasila").add({
                                "bab": sel_bab, "pertemuan": p_num, "jenis_tugas": jenis_str,
                                "judul": judul_tg, "instruksi": instruksi_tg, "tipe": "pg",
                                "target_kelas": target_k, "soal": soal_pg_list, "created_at": firestore.SERVER_TIMESTAMP
                            })
                            clear_tugas_cache(); st.success(f"{jenis_str} PG berhasil diterbitkan!"); st.rerun()

    elif menu == "📊 Rekapitulasi Nilai SMP":
        st.header("📊 Transkrip & Rekapitulasi Nilai Siswa")
        if not pilihan_kelas: st.warning("Anda belum ditugaskan mengajar kelas manapun."); st.stop()
        
        col_rk, col_rb = st.columns(2)
        with col_rk: sel_k = st.selectbox("🏫 Pilih Kelas", options=pilihan_kelas)
        with col_rb: sel_bab = st.selectbox("📚 Pilih BAB", options=daftar_bab)

        siswa_list = get_siswa_by_kelas_cached(sel_k, limit=150)
        tugas_list = [t for t in get_all_tugas_cached(limit=50) if t.get("bab") == sel_bab and is_target_sesuai_kelas(t, sel_k)]
        pengerjaan_all = get_all_pengerjaan_by_kelas_cached(sel_k, limit=300)
        p_map = {(p.get("username_siswa"), p.get("id_tugas")): p for p in pengerjaan_all}

        rekap_smp = []
        for s in siswa_list:
            un = s["username"]
            nm = s.get("nama", un)
            
            user_hadir = get_kehadiran_user(un)
            cnt_hadir = sum(1 for p in range(1, 7) if user_hadir.get(f"{sel_bab}_p{p}", {}).get("hadir"))
            score_hadir = round((cnt_hadir / 6.0) * 100)

            diskusi_scores = []
            for p in [1, 2, 4, 5]:
                d_docs = get_diskusi_by_bab_pertemuan_kelas(sel_bab, p, sel_k)
                for d in d_docs:
                    if d.get("username") == un and d.get("nilai") is not None:
                        diskusi_scores.append(float(d.get("nilai")))
            score_diskusi = round(sum(diskusi_scores) / len(diskusi_scores), 1) if diskusi_scores else 0.0

            uts_scores = []
            for tg in [t for t in tugas_list if t.get("pertemuan") == 3]:
                p = p_map.get((un, tg["id"]), {})
                if p.get("status") == "submitted" and p.get("nilai") is not None:
                    uts_scores.append(float(p.get("nilai")))
            score_uts = round(sum(uts_scores) / len(uts_scores), 1) if uts_scores else 0.0

            uas_scores = []
            for tg in [t for t in tugas_list if t.get("pertemuan") == 6]:
                p = p_map.get((un, tg["id"]), {})
                if p.get("status") == "submitted" and p.get("nilai") is not None:
                    uas_scores.append(float(p.get("nilai")))
            score_uas = round(sum(uas_scores) / len(uas_scores), 1) if uas_scores else 0.0

            score_akhir_bab = round((0.20 * score_hadir) + (0.20 * score_diskusi) + (0.30 * score_uts) + (0.30 * score_uas), 2)

            rekap_smp.append({
                "NIS": un, "Nama Siswa": nm,
                "Kehadiran (20%)": f"{score_hadir} ({cnt_hadir}/6 P)",
                "Rata Diskusi (20%)": score_diskusi,
                "UTS BAB (30%)": score_uts,
                "UAS BAB (30%)": score_uas,
                "Nilai Akhir BAB": score_akhir_bab
            })

        df_smp = pd.DataFrame(rekap_smp)
        st.dataframe(df_smp, use_container_width=True)
        csv_data = df_smp.to_csv(index=False).encode('utf-8-sig')
        st.download_button("💾 Unduh Rekap Nilai BAB (.csv)", csv_data, f"rekap_nilai_{sel_k}_{sel_bab}.csv", "text/csv", use_container_width=True)

# ==========================================
# 8. PANEL SISWA
# ==========================================
def render_siswa():
    kelas_s = user_info.get("kelas", "-")
    nama_s = user_info.get("nama", "Siswa")
    username_s = user_info.get("username", "")

    my_subs = get_user_pengerjaan_cached(username_s)
    active_quiz_id = st.session_state.get("active_quiz_id")

    # --- JIKA SISWA SEDANG MENGERJAKAN UTS / UAS BAB ---
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

        st.markdown(f"### 📝 {tg.get('jenis_tugas', 'Ujian')} - {tg.get('bab')}")
        st.caption(f"Judul: {tg.get('judul')}")
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
            if curr_page == total_soal - 1 and st.button("🚀 Kirim Jawaban Ujian", type="primary"):
                submit_jawaban_siswa(tg, username_s, nama_s, kelas_s, answers)
                st.session_state["active_quiz_id"] = None
                st.success("Ujian Berhasil Dikirim!"); st.rerun()
        st.stop()

    # --- DASHBOARD UTAMA SISWA ---
    st.markdown(f"""
        <div class="smp-header">
            <h2 style="margin:0; color:#FFFFFF;">🎓 LMS PENDIDIKAN PANCASILA SMP</h2>
            <p style="margin:0; font-size:15px;">Kelas: <b>{kelas_s}</b> | Siswa: <b>{nama_s}</b> (NIS: @{username_s})</p>
        </div>
    """, unsafe_allow_html=True)

    # Filter BAB yang HANYA AKTIF
    daftar_bab = get_bab_for_kelas(kelas_s, only_active=True)
    if not daftar_bab:
        st.info(f"Belum ada BAB pembelajaran yang aktif untuk Kelas {kelas_s}.")
        st.stop()

    sel_bab = st.selectbox("📚 Pilih BAB Pembelajaran", options=daftar_bab)
    kehadiran_dict = get_kehadiran_user(username_s)

    materi_docs = get_all_materi_cached(limit=50)
    tugas_docs = get_all_tugas_cached(limit=50)

    tabs_pertemuan = st.tabs([
        "Pertemuan 1 (Diskusi)", 
        "Pertemuan 2 (Diskusi)", 
        "Pertemuan 3 (UTS BAB)", 
        "Pertemuan 4 (Diskusi)", 
        "Pertemuan 5 (Diskusi)", 
        "Pertemuan 6 (UAS BAB)"
    ])

    for idx, tab in enumerate(tabs_pertemuan, 1):
        with tab:
            st.markdown(f"### 📌 Ruang Pembelajaran - {sel_bab} (Pertemuan {idx})")

            st.markdown("#### 1. Presensi / Kehadiran")
            key_abs = f"{sel_bab}_p{idx}"
            is_hadir = kehadiran_dict.get(key_abs, {}).get("hadir", False)
            if is_hadir:
                st.success(f"✅ Anda telah mengisi konfirmasi kehadiran pada Pertemuan {idx}.")
            else:
                if st.button(f"✋ Konfirmasi Kehadiran Pertemuan {idx}", key=f"btn_hadir_{sel_bab}_{idx}", type="primary"):
                    db.collection("kehadiran_siswa").document(f"{username_s}_{sel_bab}_p{idx}").set({
                        "username": username_s, "nama": nama_s, "kelas": kelas_s,
                        "bab": sel_bab, "pertemuan": idx, "hadir": True, "timestamp": firestore.SERVER_TIMESTAMP
                    })
                    clear_kehadiran_cache(); st.success(f"Kehadiran Pertemuan {idx} Berhasil Dicatat!"); st.rerun()

            st.divider()

            st.markdown("#### 2. Materi Pembelajaran")
            # Filter materi HANYA yang AKTIF
            m_curr = [m for m in materi_docs if m.get("bab") == sel_bab and int(m.get("pertemuan", 1)) == idx and m.get("is_active", True)]
            if not m_curr:
                st.info(f"Belum ada materi aktif pada Pertemuan {idx}.")
            else:
                for m in m_curr:
                    with st.container(border=True):
                        st.markdown(f"##### 📘 {m.get('judul')}")
                        if m.get("konten"): st.write(m.get("konten"))
                        if m.get("file_url"): st.link_button("📎 Buka Bahan Ajar / Lampiran", m.get("file_url"))

            st.divider()

            if idx in [1, 2, 4, 5]:
                st.markdown("#### 3. Forum Diskusi Siswa")
                diskusi_aktif = is_diskusi_aktif(sel_bab, idx, kelas_s)
                
                d_docs = get_diskusi_by_bab_pertemuan_kelas(sel_bab, idx, kelas_s)
                my_d = next((d for d in d_docs if d.get("username") == username_s), None)

                if my_d:
                    st.success("✅ Anda telah mengirimkan tanggapan diskusi.")
                    st.markdown(f"**Tanggapan Anda:**\n\n_{my_d.get('tanggapan')}_")
                    if my_d.get("nilai") is not None:
                        st.info(f"📊 Nilai Diskusi Guru: **{my_d.get('nilai')}** / 100\n\nCatatan Guru: {my_d.get('catatan_guru', '-')}")
                elif not diskusi_aktif:
                    st.warning("🔒 Forum diskusi untuk pertemuan ini sedang nonaktif / ditutup oleh Guru.")
                else:
                    st.caption("Silakan tuliskan tanggapan diskusi Anda sesuai materi pertemuan ini.")
                    with st.form(f"f_diskusi_mhs_{sel_bab}_{idx}"):
                        resp_diskusi = st.text_area(f"Tulis Tanggapan Diskusi Pertemuan {idx}:")
                        if st.form_submit_button("🚀 Kirim Tanggapan Diskusi"):
                            if resp_diskusi.strip():
                                db.collection("diskusi_pancasila").add({
                                    "bab": sel_bab, "pertemuan": idx, "kelas": kelas_s,
                                    "username": username_s, "nama_siswa": nama_s,
                                    "tanggapan": resp_diskusi.strip(), "nilai": None, "catatan_guru": "", "created_at": firestore.SERVER_TIMESTAMP
                                })
                                clear_diskusi_cache(); st.success("Tanggapan Diskusi Berhasil Dikirim!"); st.rerun()
                            else: st.warning("Isi tanggapan diskusi terlebih dahulu.")

            elif idx in [3, 6]:
                jenis_ujian = "UTS BAB" if idx == 3 else "UAS BAB"
                st.markdown(f"#### 3. Evaluasi {jenis_ujian}")

                t_curr = [t for t in tugas_docs if t.get("bab") == sel_bab and t.get("pertemuan") == idx and is_target_sesuai_kelas(t, kelas_s)]
                if not t_curr:
                    st.info(f"Belum ada {jenis_ujian} yang dikonfigurasi pada Pertemuan {idx}.")
                else:
                    for tg in t_curr:
                        tg_id = tg["id"]
                        sub = my_subs.get(tg_id, {})
                        status_sub = sub.get("status", "belum")

                        with st.container(border=True):
                            st.markdown(f"##### 📝 {tg.get('judul')}")
                            if tg.get("instruksi"): st.write(tg.get("instruksi"))
                            
                            if status_sub == "submitted":
                                val_n = sub.get("nilai")
                                st.success(f"✅ Sudah Dikerjakan | Nilai: **{val_n if val_n is not None else 'Sedang Dinilai Guru'}**")
                            else:
                                if st.button(f"Mulai Kerjakan {jenis_ujian} 🚀", key=f"btn_tg_s_{tg_id}", type="primary"):
                                    st.session_state["active_quiz_id"] = tg_id
                                    st.rerun()

# ==========================================
# 9. MAIN APP ROUTER
# ==========================================
if role == "superadmin":
    render_superadmin()
elif role == "guru":
    render_guru()
elif role == "siswa":
    render_siswa()
else:
    st.error("Role pengguna tidak dikenal.")
