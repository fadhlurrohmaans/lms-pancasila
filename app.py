import os
import json
import io
import re
import random
import string
import hashlib
from datetime import datetime

import streamlit as st
import streamlit.components.v1 as components
import firebase_admin
from firebase_admin import credentials, firestore
import pandas as pd
import google.generativeai as genai

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
    </style>
""", unsafe_allow_html=True)

# ==========================================
# 2. FIREBASE & CACHING
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

# --- CACHED READ FUNCTIONS ---
@st.cache_data(ttl=120)
def get_all_kelas():
    docs = db.collection("kelas").stream()
    return sorted([d.id for d in docs])

@st.cache_data(ttl=60)
def get_all_tugas_cached():
    docs = db.collection("tugas_pancasila").stream()
    return [{"id": d.id, **d.to_dict()} for d in docs]

@st.cache_data(ttl=60)
def get_all_materi_cached():
    docs = db.collection("materi_pancasila").stream()
    return [{"id": d.id, **d.to_dict()} for d in docs]

@st.cache_data(ttl=15)
def get_user_submissions_cached(username):
    docs = db.collection("jawaban_siswa").where("username_siswa", "==", username).stream()
    return {d.to_dict().get("id_tugas"): d.to_dict() for d in docs}

# --- CACHE CLEAR HELPERS ---
def clear_kelas_cache(): get_all_kelas.clear()
def clear_tugas_cache(): get_all_tugas_cached.clear()
def clear_materi_cache(): get_all_materi_cached.clear()
def clear_user_submissions_cache(): get_user_submissions_cached.clear()

# --- UTILITY HELPERS ---
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

def generate_username(nama):
    first_name = nama.strip().split()[0] if nama.strip() else "siswa"
    base_username = re.sub(r'[^a-z0-9]', '', first_name.lower())[:5] or "siswa"
    
    username, counter = base_username, 1
    while db.collection("users").document(username).get().exists:
        username = f"{base_username}{counter}"
        counter += 1
    return username
    
def generate_password(length=6):
    return ''.join(random.choices(string.ascii_lowercase + string.digits, k=length))

def is_target_sesuai_kelas(doc_data, kelas_siswa):
    target = doc_data.get("target_kelas", [])
    if not target: return True
    return kelas_siswa in target if isinstance(target, list) else target == kelas_siswa

def save_draft_to_firebase(username_s, tg_id, answers):
    # Fitur auto save draft dinonaktifkan
    pass

def submit_jawaban_siswa(tg, username_s, nama_s, kelas_s, answers, is_forced=False, is_violation=False):
    tg_id = tg["id"]
    soal_list = tg.get("soal", [])
    total_soal = len(soal_list)
    
    if is_violation:
        catatan = "⚠️ Submit Otomatis (Mencapai Limit Maksimal Pelanggaran 15x)"
    elif is_forced:
        catatan = "Di-submit Paksa oleh Guru"
    else:
        catatan = "Penilaian Otomatis Sistem"
    
    if tg.get("tipe") == "pg":
        correct_count = 0
        formatted_ans = []
        for idx_q, sq in enumerate(soal_list):
            user_a = answers[idx_q] if answers and idx_q < len(answers) else None
            formatted_ans.append(user_a if user_a is not None else -1)
            if user_a is not None and user_a == sq.get("kunci"):
                correct_count += 1
        score = round((correct_count / total_soal) * 100) if total_soal > 0 else 0

        db.collection("jawaban_siswa").add({
            "id_tugas": tg_id, "judul_tugas": tg.get("judul"), "username_siswa": username_s,
            "nama_siswa": nama_s, "kelas_siswa": kelas_s, "tipe": "pg", "jawaban": formatted_ans,
            "nilai": score, "catatan_guru": catatan, "submitted_at": firestore.SERVER_TIMESTAMP
        })
    else:
        formatted_ans = [a if a is not None else "" for a in (answers if answers else [])]
        db.collection("jawaban_siswa").add({
            "id_tugas": tg_id, "judul_tugas": tg.get("judul"), "username_siswa": username_s,
            "nama_siswa": nama_s, "kelas_siswa": kelas_s, "tipe": "essay", "soal": soal_list,
            "jawaban": formatted_ans, "nilai": None, "catatan_guru": catatan,
            "submitted_at": firestore.SERVER_TIMESTAMP
        })

    db.collection("status_ujian").document(f"{username_s}_{tg_id}").set({
        "username": username_s, "id_tugas": tg_id, "status": "submitted", "updated_at": firestore.SERVER_TIMESTAMP
    }, merge=True)
    
    clear_user_submissions_cache()
    return True

def delete_tugas_and_submissions(tugas_id):
    db.collection("tugas_pancasila").document(tugas_id).delete()
    
    j_docs = db.collection("jawaban_siswa").where("id_tugas", "==", tugas_id).stream()
    for doc in j_docs:
        db.collection("jawaban_siswa").document(doc.id).delete()
        
    s_docs = db.collection("status_ujian").where("id_tugas", "==", tugas_id).stream()
    for doc in s_docs:
        db.collection("status_ujian").document(doc.id).delete()
        
    clear_tugas_cache()
    clear_user_submissions_cache()

# ==========================================
# 3. AI EVALUATION HELPER
# ==========================================
def koreksi_essay_dengan_ai(soal_list, jawaban_list):
    api_key = st.secrets.get("GEMINI_API_KEY") or st.secrets.get("gemini", {}).get("api_key") or st.secrets.get("firebase", {}).get("GEMINI_API_KEY")
    if not api_key:
        return None, "⚠️ Key 'GEMINI_API_KEY' belum dikonfigurasi di secrets Streamlit."

    try:
        genai.configure(api_key=api_key)
        strict_schema = {
            "type": "OBJECT",
            "properties": {
                "nilai": {"type": "INTEGER"},
                "feedback": {"type": "STRING"}
            },
            "required": ["nilai", "feedback"]
        }
        generation_config = {"response_mime_type": "application/json", "response_schema": strict_schema}
        candidate_models = ['gemini-1.5-flash', 'gemini-2.0-flash', 'gemini-1.5-pro']

        total_soal = len(soal_list)
        prompt_items = []
        for i in range(total_soal):
            s = soal_list[i] if i < len(soal_list) else ""
            j = jawaban_list[i] if i < len(jawaban_list) else ""
            q_text = s.get('pertanyaan', '') if isinstance(s, dict) else str(s)
            j_text = str(j).strip() if j and str(j).strip() else '(Siswa tidak menjawab)'
            prompt_items.append(f"--- SOAL NOMOR {i+1} ---\nPertanyaan: {q_text}\nJawaban Siswa: {j_text}")

        prompt = f"Jumlah Total Soal: {total_soal}\n\n" + "\n\n".join(prompt_items)
        system_instruction = (
            "Anda adalah Guru Pendidikan Pancasila yang bijaksana dan fair.\n"
            "Tugas: Evaluasi SELURUH nomor soal essay yang diberikan secara objektif.\n\n"
            "Aturan Penilaian & Feedback:\n"
            "1. Periksa setiap nomor soal satu per satu (skala 0-100 per soal).\n"
            "2. Hitung RATA-RATA NILAI AKHIR dari seluruh soal (0-100) dan masukkan ke field 'nilai' sebagai integer.\n"
            "3. Pada field 'feedback', tuliskan rincian koreksi per nomor soal, diawali apresiasi dan diakhiri motivasi.\n"
            "4. Gunakan Bahasa Indonesia yang hangat, ramah, dan edukatif."
        )

        response, last_error = None, None
        for model_name in candidate_models:
            try:
                model = genai.GenerativeModel(model_name=model_name, system_instruction=system_instruction)
                try:
                    response = model.generate_content(prompt, generation_config=generation_config)
                except Exception:
                    response = model.generate_content(prompt, generation_config={"response_mime_type": "application/json"})
                if response and hasattr(response, 'text') and response.text.strip(): break
            except Exception as err:
                last_error = err
                continue

        if not response or not hasattr(response, 'text') or not response.text.strip():
            return None, f"AI tidak mengembalikan respon. Error terakhir: {str(last_error)}"

        result_json = json.loads(response.text.strip())
        return int(result_json.get("nilai", 0)), str(result_json.get("feedback", "")).strip() or "Terima kasih telah mengerjakan!"

    except Exception as e:
        return None, f"Gagal mengeksekusi AI: {str(e)}"

# ==========================================
# 4. AUTHENTICATION
# ==========================================
if "user" not in st.session_state:
    st.session_state["user"] = None

# --- AUTO-CREATE INITIAL SUPERADMIN IF DB EMPTY ---
admin_ref = db.collection("users").document("admin")
if not admin_ref.get().exists:
    admin_ref.set({
        "nama": "Super Admin",
        "role": "superadmin",
        "password": hash_pass("admin123"),
        "password_plain": "admin123",
        "created_at": firestore.SERVER_TIMESTAMP
    })
    st.toast("💡 Akun default awal berhasil dibuat! Username: admin | Pass: admin123")
# --------------------------------------------------

if st.session_state["user"] is None:
    st.title("🇮🇩 LMS Pendidikan Pancasila")
    st.info("💡 **Informasi**: Akun Siswa dan Guru dikelola oleh **Super Admin**.")
    
    with st.form("form_login"):
        username = st.text_input("Username").strip().lower()
        password = st.text_input("Password", type="password")
        if st.form_submit_button("Masuk / Login"):
            if username and password:
                user_ref = db.collection("users").document(username).get()
                if user_ref.exists:
                    user_data = user_ref.to_dict()
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
    st.stop()# ==========================================
# 5. SIDEBAR
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
# 6. PANEL SUPER ADMIN
# ==========================================
def render_superadmin():
    st.title("⚙️ Panel Super Admin")
    t_kelas, t_list, t_add, t_imp, t_edit, t_del = st.tabs([
        "🏫 Master Kelas", "👥 Daftar User", "➕ Buat Akun", "📥 Import/Export", "✏️ Atur Kelas", "🗑️ Hapus Akun"
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
                        db.collection("kelas").document(new_k).set({"nama": new_k, "created_at": firestore.SERVER_TIMESTAMP})
                        clear_kelas_cache()
                        st.success(f"✅ Kelas '{new_k}' ditambahkan!")
                        st.rerun()
            
            if daftar_kelas:
                st.divider()
                del_k = st.selectbox("Pilih Kelas Dihapus", daftar_kelas)
                if st.button("Hapus Kelas", type="primary"):
                    db.collection("kelas").document(del_k).delete()
                    clear_kelas_cache()
                    st.success(f"✅ Kelas '{del_k}' dihapus.")
                    st.rerun()

    with t_list:
        st.subheader("👥 Daftar Akun")
        all_user_docs = list(db.collection("users").stream())
        users = []
        for d in all_user_docs:
            u = d.to_dict() or {}
            role_user = str(u.get("role", "")).lower()
            
            if role_user == "siswa":
                kelas_display = str(u.get("kelas") or "-")
            else:
                ka = u.get("kelas_ajar")
                if isinstance(ka, list):
                    kelas_display = ", ".join([str(x) for x in ka if x]) or "-"
                elif ka:
                    kelas_display = str(ka)
                else:
                    kelas_display = "-"

            users.append({
                "Username": d.id,
                "Nama": u.get("nama", "-"),
                "Role": role_user.upper(),
                "Kelas": kelas_display
            })
            
        if users:
            st.dataframe(pd.DataFrame(users), use_container_width=True)
    with t_add:
        st.subheader("➕ Buat Akun Satuan")
        daftar_kelas = get_all_kelas()
        new_role = st.selectbox("Role", ["Siswa", "Guru", "Superadmin"])
        
        with st.form("form_add_user", clear_on_submit=True):
            nama = st.text_input("Nama Lengkap")
            uname = st.text_input("Username").strip().lower()
            pwd = st.text_input("Password", type="password")
            
            k_siswa = st.selectbox("Pilih Kelas", options=daftar_kelas) if new_role == "Siswa" and daftar_kelas else None
            k_guru = st.multiselect("Pilih Kelas Ajar", options=daftar_kelas) if new_role == "Guru" and daftar_kelas else None
            
            if st.form_submit_button("Buat Akun"):
                if nama and uname and pwd:
                    if db.collection("users").document(uname).get().exists:
                        st.error("Username sudah ada!")
                    else:
                        payload = {"nama": nama, "password": hash_pass(pwd), "password_plain": pwd, "role": new_role.lower(), "created_at": firestore.SERVER_TIMESTAMP}
                        if new_role == "Siswa": payload["kelas"] = k_siswa
                        elif new_role == "Guru": payload["kelas_ajar"] = k_guru
                        db.collection("users").document(uname).set(payload)
                        st.success(f"✅ Akun '{uname}' berhasil dibuat!")
                        st.rerun()

    with t_imp:
        st.subheader("📥 Import User & 📤 Export Data")
        
        st.markdown("### 📄 Unduh Template Import")
        st.caption("Gunakan template di bawah ini agar format data sesuai saat melakukan upload.")
        
        df_tpl_siswa = pd.DataFrame([
            {"nama": "Ahmad Santoso", "kelas": "X-1"},
            {"nama": "Siti Nurhaliza", "kelas": "X-2"}
        ])
        csv_tpl_siswa = df_tpl_siswa.to_csv(index=False).encode('utf-8-sig')

        df_tpl_guru = pd.DataFrame([
            {"nama": "Budi Gunawan, S.Pd.", "kelas": "X-1, X-2"},
            {"nama": "Dewi Sartika, M.Pd.", "kelas": "XI-1, XI-2"}
        ])
        csv_tpl_guru = df_tpl_guru.to_csv(index=False).encode('utf-8-sig')

        c_tpl_s, c_tpl_g = st.columns(2)
        with c_tpl_s:
            st.download_button(
                label="📄 Unduh Template Siswa (.csv)",
                data=csv_tpl_siswa,
                file_name="template_import_siswa.csv",
                mime="text/csv",
                use_container_width=True
            )
        with c_tpl_g:
            st.download_button(
                label="📄 Unduh Template Guru (.csv)",
                data=csv_tpl_guru,
                file_name="template_import_guru.csv",
                mime="text/csv",
                use_container_width=True
            )

        st.divider()

        target_role_imp = st.radio("Pilih Peran User yang Akan Di-import:", ["Siswa", "Guru"], horizontal=True)
        st.info("💡 **Format File Import (.csv / .xlsx)**: Wajib memiliki 2 kolom utama: **`nama`** dan **`kelas`**.\n\n*Catatan untuk Guru:* Jika mengajar lebih dari 1 kelas, pisahkan nama kelas dengan koma (contoh: `X-1, X-2`).")
        
        col_imp, col_exp = st.columns(2)
        with col_imp:
            up_file = st.file_uploader(f"Unggah File Data {target_role_imp} (.csv / .xlsx)", type=["csv", "xlsx"])
            if up_file and st.button(f"🚀 Import {target_role_imp}", type="primary", use_container_width=True):
                df = safe_read_uploaded_file(up_file)
                df.columns = [str(c).strip().lower() for c in df.columns]
                
                if "nama" in df.columns and "kelas" in df.columns:
                    role_str = target_role_imp.lower()
                    exist_map = {
                        d.to_dict().get("nama", "").strip().lower(): d.id 
                        for d in db.collection("users").where("role", "==", role_str).stream()
                    }
                    c_new, c_up = 0, 0
                    
                    for _, r in df.iterrows():
                        n_str = str(r["nama"]).strip()
                        k_str = str(r["kelas"]).strip()
                        if not n_str or pd.isna(r["nama"]): continue
                        
                        n_key = n_str.lower()
                        
                        if role_str == "guru":
                            list_kelas = [k.strip() for k in k_str.split(",") if k.strip()]
                            if n_key in exist_map:
                                db.collection("users").document(exist_map[n_key]).update({"kelas_ajar": list_kelas})
                                c_up += 1
                            else:
                                un = generate_username(n_str)
                                pw = generate_password()
                                db.collection("users").document(un).set({
                                    "nama": n_str, "password": hash_pass(pw), "password_plain": pw,
                                    "role": "guru", "kelas_ajar": list_kelas, "created_at": firestore.SERVER_TIMESTAMP
                                })
                                c_new += 1
                        else:
                            if n_key in exist_map:
                                db.collection("users").document(exist_map[n_key]).update({"kelas": k_str})
                                c_up += 1
                            else:
                                un = generate_username(n_str)
                                pw = generate_password()
                                db.collection("users").document(un).set({
                                    "nama": n_str, "password": hash_pass(pw), "password_plain": pw,
                                    "role": "siswa", "kelas": k_str, "created_at": firestore.SERVER_TIMESTAMP
                                })
                                c_new += 1
                    
                    st.success(f"✅ Selesai: {c_new} akun baru dibuat, {c_up} akun diperbarui.")
                    st.rerun()
                else:
                    st.error("❌ Format kolom file tidak sesuai! Pastikan terdapat kolom **nama** dan **kelas**.")

        with col_exp:
            data_siswa = [
                {"Nama": (u := d.to_dict()).get("nama"), "Username": d.id, "Password": u.get("password_plain", "*****"), "Kelas": u.get("kelas", "")}
                for d in db.collection("users").where("role", "==", "siswa").stream()
            ]
            if data_siswa:
                df_exp = pd.DataFrame(data_siswa)
                st.download_button("💾 Unduh CSV Data Siswa Eksisting", df_exp.to_csv(index=False).encode('utf-8'), "data_siswa_eksisting.csv", "text/csv", use_container_width=True)

    with t_edit:
        st.subheader("✏️ Atur Kelas User (Siswa & Guru)")
        docs = list(db.collection("users").stream())
        users_map = {
            d.id: f"{d.to_dict().get('nama')} (@{d.id}) - [{d.to_dict().get('role', '').upper()}]" 
            for d in docs if d.to_dict().get("role") in ["siswa", "guru"]
        }
        daftar_k = get_all_kelas()
        
        if not users_map:
            st.info("Belum ada akun Guru atau Siswa terdaftar.")
        elif not daftar_k:
            st.warning("⚠️ Master Kelas belum diisi. Tambahkan kelas di tab 'Master Kelas' terlebih dahulu.")
        else:
            target_uid = st.selectbox("Pilih Pengguna yang Akan Diatur", list(users_map.keys()), format_func=lambda x: users_map[x])
            u_data = db.collection("users").document(target_uid).get().to_dict()
            u_role = u_data.get("role", "")
            
            with st.form(key=f"form_edit_user_k_{target_uid}"):
                if u_role == "siswa":
                    curr_k = u_data.get("kelas", "")
                    idx_k = daftar_k.index(curr_k) if curr_k in daftar_k else 0
                    new_k = st.selectbox("Pilih Kelas Baru Siswa", options=daftar_k, index=idx_k, key=f"sb_siswa_{target_uid}")
                    
                    if st.form_submit_button("💾 Simpan Perubahan Kelas Siswa", type="primary"):
                        db.collection("users").document(target_uid).update({"kelas": new_k})
                        st.success(f"✅ Kelas untuk siswa '{u_data.get('nama')}' berhasil diubah ke {new_k}!")
                        st.rerun()
                else:
                    curr_ka = u_data.get("kelas_ajar", [])
                    if isinstance(curr_ka, str): 
                        curr_ka = [curr_ka]
                    valid_defaults = [k for k in curr_ka if k in daftar_k]
                    
                    st.write(f"👤 **Pengaturan Kelas Ajar untuk Guru:** {u_data.get('nama')}")
                    new_ka = st.multiselect(
                        "Tentukan Kelas Ajar Guru (Bisa Pilih Lebih dari Satu):", 
                        options=daftar_k, 
                        default=valid_defaults,
                        key=f"ms_guru_{target_uid}"
                    )
                    
                    if st.form_submit_button("💾 Simpan Perubahan Kelas Ajar Guru", type="primary"):
                        db.collection("users").document(target_uid).update({"kelas_ajar": new_ka})
                        st.success(f"✅ Berhasil memperbarui kelas ajar untuk Guru '{u_data.get('nama')}'!")
                        st.rerun()

    with t_del:
        st.subheader("🗑️ Hapus Akun")
        all_u = {d.id: f"{d.to_dict().get('nama')} (@{d.id})" for d in db.collection("users").stream() if d.id != user_info["username"]}
        if all_u:
            target_del = st.selectbox("Pilih Akun Dihapus", list(all_u.keys()), format_func=lambda x: all_u[x])
            if st.button("Hapus Akun", type="primary"):
                db.collection("users").document(target_del).delete()
                st.success("✅ Akun berhasil dihapus!")
                st.rerun()

# ==========================================
# 7. PANEL GURU
# ==========================================
def render_guru():
    st.title("🇮🇩 Panel Guru")
    menu = st.sidebar.radio("📌 Menu Guru", ["📖 Kelola Materi", "📝 Buat & Kelola Tugas", "📊 Rekap & Penilaian", "📜 Daftar Nilai"])
    pilihan_kelas = user_info.get("kelas_ajar") or get_all_kelas()
    if isinstance(pilihan_kelas, str): pilihan_kelas = [pilihan_kelas]

    if menu == "📖 Kelola Materi":
        st.header("📖 Kelola Materi Pembelajaran")
        t_list, t_buat = st.tabs(["📋 Daftar Materi", "➕ Tambah Materi Baru"])
        
        with t_list:
            materi_docs = get_all_materi_cached()
            if not materi_docs:
                st.info("Belum ada materi pembelajaran yang diunggah.")
            else:
                for m in materi_docs:
                    m_id = m["id"]
                    target_str = ", ".join(m.get("target_kelas", [])) if m.get("target_kelas") else "Semua Kelas"
                    
                    with st.container(border=True):
                        col_info, col_aksi = st.columns([3, 1])
                        
                        with col_info:
                            st.markdown(f"### 📘 [{m.get('bab')}] {m.get('judul')}")
                            st.caption(f"🏫 **Target Kelas:** {target_str}")
                            if m.get("konten"): st.write(m.get("konten"))
                            if m.get("file_url"): st.link_button("📎 Buka Lampiran File", m.get("file_url"))

                        with col_aksi:
                            with st.popover("✏️ Edit"):
                                with st.form(key=f"form_edit_materi_{m_id}"):
                                    e_bab = st.text_input("Bab / Unit", value=m.get("bab", ""), key=f"e_bab_{m_id}")
                                    e_judul = st.text_input("Judul Materi", value=m.get("judul", ""), key=f"e_jud_{m_id}")
                                    e_target = st.multiselect("Target Kelas", options=pilihan_kelas, default=m.get("target_kelas", pilihan_kelas) if m.get("target_kelas") else pilihan_kelas, key=f"e_tgt_{m_id}")
                                    e_konten = st.text_area("Deskripsi / Teks Materi", value=m.get("konten", ""), key=f"e_kon_{m_id}")
                                    e_file_url = st.text_input("🔗 Link Lampiran", value=m.get("file_url", "") or "", key=f"e_url_{m_id}")

                                    if st.form_submit_button("💾 Simpan Perubahan"):
                                        if e_bab and e_judul and e_target:
                                            db.collection("materi_pancasila").document(m_id).update({
                                                "bab": e_bab, "judul": e_judul, "target_kelas": e_target,
                                                "konten": e_konten, "file_url": e_file_url.strip() if e_file_url else None,
                                                "updated_at": firestore.SERVER_TIMESTAMP
                                            })
                                            clear_materi_cache()
                                            st.success("✅ Perubahan berhasil disimpan!")
                                            st.rerun()

                            if st.button("🗑️ Hapus", key=f"btn_del_mat_{m_id}", type="primary"):
                                db.collection("materi_pancasila").document(m_id).delete()
                                clear_materi_cache()
                                st.success("✅ Materi berhasil dihapus!")
                                st.rerun()

        with t_buat:
            with st.form(key="form_tambah_materi_baru", clear_on_submit=True):
                bab = st.text_input("Bab / Unit", key="add_bab")
                judul = st.text_input("Judul Materi", key="add_judul")
                target_k = st.multiselect("Target Kelas", options=pilihan_kelas, default=pilihan_kelas, key="add_target")
                konten = st.text_area("Deskripsi / Teks Materi (Opsional)", key="add_konten")
                file_url = st.text_input("🔗 Link Lampiran Dokumen", key="add_url")

                if st.form_submit_button("📁 Simpan Materi Baru"):
                    if bab and judul and target_k:
                        db.collection("materi_pancasila").add({
                            "bab": bab, "judul": judul, "target_kelas": target_k, "konten": konten,
                            "file_url": file_url.strip() if file_url else None, "created_at": firestore.SERVER_TIMESTAMP
                        })
                        clear_materi_cache()
                        st.success("✅ Materi baru berhasil ditambahkan!")
                        st.rerun()

    elif menu == "📝 Buat & Kelola Tugas":
        st.header("📝 Buat & Kelola Tugas")
        t_list, t_buat, t_edit, t_imp = st.tabs(["📋 Daftar", "➕ Buat Tugas", "✏️ Edit Tugas", "📥 Import Soal"])

        with t_list:
            tugas_cached = get_all_tugas_cached()
            if not tugas_cached:
                st.info("Belum ada tugas/kuis yang dibuat.")
            else:
                for tg in tugas_cached:
                    target_str = ", ".join(tg.get("target_kelas", [])) if tg.get("target_kelas") else "Semua"
                    is_published = tg.get("status", "terbit") == "terbit"
                    status_label = "🟢 Terbit" if is_published else "🔴 Draft (Tidak Terbit)"
                    jenis_label = "🎯 Ulangan Harian" if tg.get("jenis_tugas", "Ulangan Harian") == "Ulangan Harian" else "📌 Tugas Biasa"
                    
                    with st.expander(f"[{'PG' if tg.get('tipe')=='pg' else 'Essay'}] [{jenis_label}] {tg.get('judul')} ({status_label} | Kelas: {target_str})"):
                        st.write(f"**Jenis Pelaksanaan:** {jenis_label}")
                        st.write(f"**Instruksi:** {tg.get('instruksi')}")
                        st.write(f"**Jumlah Soal:** {len(tg.get('soal', []))}")
                        st.write(f"**Status Publikasi:** {status_label}")
                        
                        col_t1, col_t2 = st.columns(2)
                        with col_t1:
                            new_st = "draft" if is_published else "terbit"
                            btn_st_label = "🔴 Ubah ke Draft" if is_published else "🟢 Terbitkan Tugas"
                            if st.button(btn_st_label, key=f"toggle_st_{tg['id']}"):
                                db.collection("tugas_pancasila").document(tg["id"]).update({"status": new_st})
                                clear_tugas_cache()
                                st.rerun()
                        with col_t2:
                            if st.button(f"🗑️ Hapus Tugas", key=f"del_{tg['id']}", type="primary"):
                                delete_tugas_and_submissions(tg["id"])
                                st.success("✅ Berhasil! Tugas beserta seluruh riwayat nilainya telah dihapus.")
                                st.rerun()

        with t_buat:
            judul = st.text_input("Judul Tugas")
            instruksi = st.text_area("Instruksi")
            target_k = st.multiselect("Target Kelas", options=pilihan_kelas, default=pilihan_kelas)
            jenis_tugas = st.radio(
                "Jenis Pelaksanaan Tugas / Ujian", 
                ["Ulangan Harian", "Tugas Biasa"], 
                help="Ulangan Harian: Memakai anti-kecurangan & otomatis terkunci jika ke-refresh/keluar. Tugas Biasa: Bebas pengerjaan tanpa penguncian.",
                horizontal=True
            )
            status_t = st.radio("Status Publikasi", ["terbit", "draft"], format_func=lambda x: "🟢 Terbit (Langsung Tampil ke Siswa)" if x == "terbit" else "🔴 Draft (Tidak Terbit)", horizontal=True)
            tipe_t = st.radio("Tipe Soal", ["Pilihan Ganda", "Essay"])

            if tipe_t == "Pilihan Ganda":
                n_soal = st.number_input("Jumlah Soal", 1, 50, 5)
                with st.form("form_pg"):
                    soal_list = []
                    for i in range(n_soal):
                        q = st.text_area(f"Soal #{i+1}", key=f"q_{i}")
                        c1, c2 = st.columns(2)
                        o0, o1 = c1.text_input(f"A #{i+1}", key=f"a_{i}"), c1.text_input(f"B #{i+1}", key=f"b_{i}")
                        o2, o3 = c2.text_input(f"C #{i+1}", key=f"c_{i}"), c2.text_input(f"D #{i+1}", key=f"d_{i}")
                        k = st.selectbox(f"Kunci #{i+1}", [0, 1, 2, 3], format_func=lambda x: ['A','B','C','D'][x], key=f"k_{i}")
                        soal_list.append({"pertanyaan": q, "opsi": [o0, o1, o2, o3], "kunci": k})
                    
                    if st.form_submit_button("Simpan Tugas PG"):
                        if judul and target_k:
                            db.collection("tugas_pancasila").add({
                                "judul": judul, "instruksi": instruksi, "tipe": "pg", "target_kelas": target_k,
                                "jenis_tugas": jenis_tugas, "status": status_t, "soal": soal_list, "created_at": firestore.SERVER_TIMESTAMP
                            })
                            clear_tugas_cache()
                            st.success("✅ Berhasil! Tugas Pilihan Ganda berhasil disimpan.")
                            st.rerun()
            else:
                n_essay = st.number_input("Jumlah Soal Essay", 1, 10, 2)
                with st.form("form_essay"):
                    soal_list = [{"pertanyaan": st.text_area(f"Soal #{i+1}", key=f"qe_{i}")} for i in range(n_essay)]
                    if st.form_submit_button("Simpan Tugas Essay"):
                        if judul and target_k:
                            db.collection("tugas_pancasila").add({
                                "judul": judul, "instruksi": instruksi, "tipe": "essay", "target_kelas": target_k,
                                "jenis_tugas": jenis_tugas, "status": status_t, "soal": soal_list, "created_at": firestore.SERVER_TIMESTAMP
                            })
                            clear_tugas_cache()
                            st.success("✅ Berhasil! Tugas Essay berhasil disimpan.")
                            st.rerun()

        with t_edit:
            st.subheader("✏️ Edit Tugas & Soal")
            tugas_list = get_all_tugas_cached()
            if tugas_list:
                tg_map = {t["id"]: f"[{'🟢 Terbit' if t.get('status', 'terbit') == 'terbit' else '🔴 Draft'}] [{t.get('jenis_tugas', 'Ulangan Harian')}] {t.get('judul')}" for t in tugas_list}
                sel_id = st.selectbox("Pilih Tugas yang Akan Diedit", list(tg_map.keys()), format_func=lambda x: tg_map[x])
                target_tg = next(t for t in tugas_list if t["id"] == sel_id)

                with st.form(key=f"form_update_tg_{sel_id}"):
                    e_judul = st.text_input("Judul Tugas", value=target_tg.get("judul", ""), key=f"e_judul_{sel_id}")
                    e_instruksi = st.text_area("Instruksi Tugas", value=target_tg.get("instruksi", ""), key=f"e_instruksi_{sel_id}")
                    e_target = st.multiselect("Target Kelas", options=pilihan_kelas, default=target_tg.get("target_kelas", pilihan_kelas), key=f"e_target_{sel_id}")
                    
                    curr_jenis = target_tg.get("jenis_tugas", "Ulangan Harian")
                    e_jenis = st.radio(
                        "Jenis Pelaksanaan Tugas / Ujian",
                        ["Ulangan Harian", "Tugas Biasa"],
                        index=0 if curr_jenis == "Ulangan Harian" else 1,
                        horizontal=True,
                        key=f"e_jenis_{sel_id}"
                    )
                    
                    e_status = st.radio(
                        "Status Publikasi", 
                        ["terbit", "draft"], 
                        index=0 if target_tg.get("status", "terbit") == "terbit" else 1, 
                        format_func=lambda x: "🟢 Terbit (Tampil ke Siswa)" if x == "terbit" else "🔴 Draft (Sembunyikan)", 
                        horizontal=True,
                        key=f"e_status_{sel_id}"
                    )
                    
                    tipe_tugas = target_tg.get("tipe", "pg")
                    existing_soal = target_tg.get("soal", [])
                    updated_soal = []

                    if tipe_tugas == "pg":
                        for i, s in enumerate(existing_soal):
                            st.markdown(f"**Soal #{i+1}**")
                            q_val = s.get("pertanyaan", "") if isinstance(s, dict) else str(s)
                            e_q = st.text_area(f"Pertanyaan #{i+1}", value=q_val, key=f"e_q_{sel_id}_{i}")
                            opsi = s.get("opsi", ["", "", "", ""]) if isinstance(s, dict) else ["", "", "", ""]
                            c1, c2 = st.columns(2)
                            e_o0 = c1.text_input(f"A #{i+1}", value=opsi[0] if len(opsi)>0 else "", key=f"e_a_{sel_id}_{i}")
                            e_o1 = c1.text_input(f"B #{i+1}", value=opsi[1] if len(opsi)>1 else "", key=f"e_b_{sel_id}_{i}")
                            e_o2 = c2.text_input(f"C #{i+1}", value=opsi[2] if len(opsi)>2 else "", key=f"e_c_{sel_id}_{i}")
                            e_o3 = c2.text_input(f"D #{i+1}", value=opsi[3] if len(opsi)>3 else "", key=f"e_d_{sel_id}_{i}")
                            curr_k = s.get("kunci", 0) if isinstance(s, dict) else 0
                            curr_idx = int(curr_k) if isinstance(curr_k, int) and 0 <= int(curr_k) <= 3 else 0
                            e_k = st.selectbox(f"Kunci Jawaban #{i+1}", [0, 1, 2, 3], index=curr_idx, format_func=lambda x: ['A','B','C','D'][x], key=f"e_k_{sel_id}_{i}")
                            updated_soal.append({"pertanyaan": e_q, "opsi": [e_o0, e_o1, e_o2, e_o3], "kunci": e_k})
                    else:
                        for i, s in enumerate(existing_soal):
                            q_val = s.get("pertanyaan", "") if isinstance(s, dict) else str(s)
                            e_q = st.text_area(f"Soal Essay #{i+1}", value=q_val, key=f"e_qe_{sel_id}_{i}")
                            updated_soal.append({"pertanyaan": e_q})

                    if st.form_submit_button("💾 Perbarui Tugas & Soal"):
                        db.collection("tugas_pancasila").document(sel_id).update({
                            "judul": e_judul, "instruksi": e_instruksi, "target_kelas": e_target,
                            "jenis_tugas": e_jenis, "status": e_status, "soal": updated_soal, "updated_at": firestore.SERVER_TIMESTAMP
                        })
                        clear_tugas_cache()
                        st.success("✅ Berhasil! Informasi tugas dan soal telah diperbarui.")
                        st.rerun()

        with t_imp:
            st.subheader("📥 Import Soal Tugas (.csv / .xlsx)")
            st.info("💡 **Unduh Template Soal:** Gunakan tombol di bawah ini untuk mengunduh format CSV yang sesuai.")
            
            df_tpl_pg = pd.DataFrame([
                {
                    "pertanyaan": "Apa lambang sila ke-1 Pancasila?",
                    "opsi_a": "Bintang", "opsi_b": "Rantai", "opsi_c": "Pohon Beringin", "opsi_d": "Kepala Banteng",
                    "kunci": "A"
                },
                {
                    "pertanyaan": "Siapa yang mengusulkan nama Pancasila pada 1 Juni 1945?",
                    "opsi_a": "Ir. Soekarno", "opsi_b": "Drs. Mohammad Hatta", "opsi_c": "Mr. Muhammad Yamin", "opsi_d": "Prof. Dr. Soepomo",
                    "kunci": "A"
                }
            ])
            csv_pg = df_tpl_pg.to_csv(index=False).encode('utf-8-sig')

            df_tpl_essay = pd.DataFrame([
                {"pertanyaan": "Jelaskan penerapan nilai-nilai Pancasila sila ke-3 dalam lingkungan sekolah!"},
                {"pertanyaan": "Mengapa musyawarah sangat penting dalam mengambil keputusan bersama?"}
            ])
            csv_essay = df_tpl_essay.to_csv(index=False).encode('utf-8-sig')

            c_tpl1, c_tpl2 = st.columns(2)
            with c_tpl1:
                st.download_button("📄 Unduh Template PG (.csv)", data=csv_pg, file_name="template_soal_pg.csv", mime="text/csv", use_container_width=True)
            with c_tpl2:
                st.download_button("📄 Unduh Template Essay (.csv)", data=csv_essay, file_name="template_soal_essay.csv", mime="text/csv", use_container_width=True)

            st.divider()

            up_soal = st.file_uploader("Upload File Soal (.csv / .xlsx)", type=["csv", "xlsx"])
            imp_judul = st.text_input("Judul Tugas Baru")
            imp_instruksi = st.text_area("Instruksi (Opsional)")
            imp_target = st.multiselect("Target Kelas Import", options=pilihan_kelas, default=pilihan_kelas)
            imp_jenis = st.radio("Jenis Pelaksanaan Import", ["Ulangan Harian", "Tugas Biasa"], horizontal=True)
            imp_status = st.radio("Status Publikasi Import", ["terbit", "draft"], format_func=lambda x: "🟢 Terbit" if x == "terbit" else "🔴 Draft", horizontal=True)
            imp_tipe = st.selectbox("Tipe Soal Import", ["pg", "essay"])

            if up_soal and imp_judul and imp_target and st.button("🚀 Import Soal Sekarang", type="primary"):
                df_s = safe_read_uploaded_file(up_soal)
                df_s.columns = [str(c).strip().lower() for c in df_s.columns]
                
                q_col = next((c for c in ["pertanyaan", "soal", "question"] if c in df_s.columns), None)

                if not q_col:
                    st.error(f"❌ Kolom pertanyaan tidak ditemukan. Kolom terdeteksi: `{list(df_s.columns)}`. Pastikan ada kolom **pertanyaan** atau **soal**.")
                    st.stop()

                parsed_s = []
                
                if imp_tipe == "pg":
                    required_pg_cols = ["opsi_a", "opsi_b", "opsi_c", "opsi_d", "kunci"]
                    missing_cols = [c for c in required_pg_cols if c not in df_s.columns]
                    
                    if missing_cols:
                        st.error(f"❌ File PG kekurangan kolom: `{missing_cols}`. Pastikan menggunakan template yang telah disediakan.")
                        st.stop()

                    key_m = {'a': 0, 'b': 1, 'c': 2, 'd': 3, '0': 0, '1': 1, '2': 2, '3': 3}
                    for _, r in df_s.iterrows():
                        if pd.isna(r[q_col]): continue
                        parsed_s.append({
                            "pertanyaan": str(r[q_col]),
                            "opsi": [str(r["opsi_a"]), str(r["opsi_b"]), str(r["opsi_c"]), str(r["opsi_d"])],
                            "kunci": key_m.get(str(r["kunci"]).strip().lower(), 0)
                        })
                else:
                    for _, r in df_s.iterrows():
                        if pd.isna(r[q_col]): continue
                        parsed_s.append({"pertanyaan": str(r[q_col])})

                if not parsed_s:
                    st.error("⚠️ Tidak ada data soal valid yang dibaca dari file.")
                    st.stop()

                db.collection("tugas_pancasila").add({
                    "judul": imp_judul, "instruksi": imp_instruksi, "tipe": imp_tipe, "target_kelas": imp_target,
                    "jenis_tugas": imp_jenis, "status": imp_status, "soal": parsed_s, "created_at": firestore.SERVER_TIMESTAMP
                })
                clear_tugas_cache()
                st.success(f"✅ Berhasil! {len(parsed_s)} soal berhasil diimpor.")
                st.rerun()

    elif menu == "📊 Rekap & Penilaian":
        st.header("📊 Rekap & Penilaian Tugas Per Kelas")
        if not pilihan_kelas: st.warning("⚠️ Anda belum ditugaskan mengajar kelas manapun."); st.stop()

        col_k, col_t = st.columns(2)
        with col_k: selected_kelas = st.selectbox("🏫 Pilih Kelas Ajar", options=pilihan_kelas)

        tugas_kelas = [d for d in get_all_tugas_cached() if is_target_sesuai_kelas(d, selected_kelas)]
        if not tugas_kelas: st.info(f"Belum ada tugas untuk Kelas **{selected_kelas}**."); st.stop()

        with col_t:
            tg_options = {t["id"]: f"[{'🟢 Terbit' if t.get('status', 'terbit') == 'terbit' else '🔴 Draft'}] [{t.get('jenis_tugas', 'Ulangan Harian')}] [{t.get('tipe', '').upper()}] {t.get('judul')}" for t in tugas_kelas}
            selected_tugas_id = st.selectbox("📝 Pilih Tugas", list(tg_options.keys()), format_func=lambda x: tg_options[x])
            selected_tugas = next(t for t in tugas_kelas if t["id"] == selected_tugas_id)

        siswa_docs = db.collection("users").where("role", "==", "siswa").where("kelas", "==", selected_kelas).stream()
        siswa_list = [{"username": d.id, **d.to_dict()} for d in siswa_docs]

        sub_docs = db.collection("jawaban_siswa").where("id_tugas", "==", selected_tugas_id).where("kelas_siswa", "==", selected_kelas).stream()
        sub_list = [{"id": d.id, **d.to_dict()} for d in sub_docs]
        sub_map = {s.get("username_siswa"): s for s in sub_list}

        status_docs = db.collection("status_ujian").where("id_tugas", "==", selected_tugas_id).stream()
        status_map = {d.to_dict().get("username"): d.to_dict() for d in status_docs}

        siswa_belum_submit = [s for s in siswa_list if s["username"] not in sub_map]
        
        st.divider()
        col_sub_info, col_sub_btn = st.columns([2, 1])
        with col_sub_info:
            st.write(f"👥 Total Siswa: **{len(siswa_list)}** | Sudah: **{len(sub_list)}** | Belum: **{len(siswa_belum_submit)}**")
        with col_sub_btn:
            if siswa_belum_submit and st.button("⚡ Submit Paksa Semua Siswa Belum", type="primary", use_container_width=True):
                for s_unsub in siswa_belum_submit:
                    submit_jawaban_siswa(
                        selected_tugas, s_unsub["username"], s_unsub.get("nama", s_unsub["username"]), 
                        selected_kelas, answers=[], is_forced=True
                    )
                st.success(f"✅ Berhasil melakukan Submit Paksa untuk {len(siswa_belum_submit)} siswa!")
                st.rerun()

        rekap_rows = []
        is_ulangan_task = selected_tugas.get("jenis_tugas", "Ulangan Harian") == "Ulangan Harian"
        
        for s in siswa_list:
            un = s["username"]
            sub = sub_map.get(un)
            st_data = status_map.get(un, {})
            v_count = st_data.get("violation_count", 0)
            ijin = st_data.get("ijin_guru", False)
            st_ujian = st_data.get("status", "")
            
            rekap_rows.append({
                "Username": un, "Nama Siswa": s.get("nama", un),
                "Status": "✅ Sudah" if sub else ("⏳ Sedang Mengerjakan" if st_ujian == "in_progress" else "❌ Belum"),
                "Jumlah Pelanggaran": f"⚠️ {v_count}x" if (is_ulangan_task and v_count > 0) else ("0" if is_ulangan_task else "N/A"),
                "Status Akses": ("✅ Diberikan Izin" if ijin else ("🔒 Terkunci (Keluar/Refresh)" if (st_ujian == "in_progress" and not ijin) else "-")) if is_ulangan_task else "Bebas (Tugas Biasa)",
                "Nilai": sub.get("nilai") if sub and sub.get("nilai") is not None else ("Belum Dinilai" if sub else "-"),
                "Catatan Guru": sub.get("catatan_guru", "-") if sub else "-"
            })

        t_rekap, t_koreksi, t_analisis, t_kontrol = st.tabs([
            "📋 Rekap Pengerjaan", 
            "✏️ Koreksi & Penilaian", 
            "📈 Analisis & Validitas PG", 
            "🔓 Kontrol Izin & Buka Kunci"
        ])

        with t_rekap:
            st.dataframe(pd.DataFrame(rekap_rows), use_container_width=True)

        with t_koreksi:
            if not sub_list:
                st.info("Belum ada siswa yang mengumpulkan tugas.")
            else:
                for sub in sub_list:
                    sub_id = sub["id"]
                    val_key, cat_key = f"n_{sub_id}", f"c_{sub_id}"
                    if val_key not in st.session_state: st.session_state[val_key] = int(sub.get("nilai", 80)) if sub.get("nilai") is not None else 80
                    if cat_key not in st.session_state: st.session_state[cat_key] = str(sub.get("catatan_guru", ""))

                    with st.expander(f"👤 {sub.get('nama_siswa')} — Nilai: {sub.get('nilai', 'Belum')}"):
                        soal_items = sub.get("soal", selected_tugas.get("soal", []))
                        jawaban_items = sub.get("jawaban", [])

                        for idx, (q, a) in enumerate(zip(soal_items, jawaban_items), 1):
                            q_text = q.get('pertanyaan') if isinstance(q, dict) else q
                            st.write(f"**{idx}. {q_text}**")
                            if sub.get("tipe") == "pg":
                                opsi_list = q.get("opsi", [])
                                ans_idx = a if isinstance(a, int) else 0
                                ans_text = opsi_list[ans_idx] if ans_idx < len(opsi_list) and ans_idx >= 0 else str(a)
                                is_correct = (ans_idx == q.get("kunci", 0))
                                st.write(f"Jawaban: **{ans_text}** ({'✅ Benar' if is_correct else '❌ Salah'})")
                            else:
                                st.info(a or "(Kosong)")

                        if selected_tugas.get("tipe") == "essay" and st.button("🤖 Auto Koreksi AI", key=f"ai_{sub_id}"):
                            with st.spinner("Analyzing..."):
                                val, fb = koreksi_essay_dengan_ai(soal_items, jawaban_items)
                            if val is not None:
                                st.session_state[val_key] = int(val)
                                st.session_state[cat_key] = str(fb)
                                st.success("Dimuat dari AI! Silakan klik Simpan.")
                                st.rerun()

                        with st.form(key=f"f_eval_{sub_id}"):
                            n_in = st.number_input("Nilai (0-100)", 0, 100, key=val_key)
                            c_in = st.text_area("Catatan Guru", key=cat_key)
                            if st.form_submit_button("💾 Simpan Perubahan"):
                                db.collection("jawaban_siswa").document(sub_id).update({"nilai": n_in, "catatan_guru": c_in})
                                clear_user_submissions_cache()
                                st.success("✅ Tersimpan!")
                                st.rerun()

        with t_analisis:
            st.subheader("📈 Analisis Butir Soal & Uji Validitas (PG)")
            if selected_tugas.get("tipe") != "pg":
                st.info("ℹ️ Analisis dan uji validitas saat ini khusus untuk tugas bertipe **Pilihan Ganda**.")
            elif not sub_list:
                st.info("ℹ️ Belum ada siswa yang mengumpulkan tugas ini untuk dianalisis.")
            else:
                soal_master = selected_tugas.get("soal", [])
                total_responden = len(sub_list)
                scores = [s.get("nilai", 0) for s in sub_list if s.get("nilai") is not None]

                m1, m2, m3, m4 = st.columns(4)
                m1.metric("Total Responden", f"{total_responden} Siswa")
                m2.metric("Rata-rata Kelas", f"{round(sum(scores)/len(scores), 1) if scores else 0}")
                m3.metric("Nilai Tertinggi", f"{max(scores) if scores else 0}")
                m4.metric("Nilai Terendah", f"{min(scores) if scores else 0}")

                st.divider()
                st.markdown("### 📋 Tingkat Kesukaran, Validitas & Distribusi Jawaban")

                matrix_data = []
                for sub in sub_list:
                    ans_list = sub.get("jawaban", [])
                    row_scores = {
                        f"Q_{idx}": 1 if (isinstance(ans_list[idx] if idx < len(ans_list) else None, int) and ans_list[idx] == q.get("kunci", 0)) else 0
                        for idx, q in enumerate(soal_master)
                    }
                    row_scores["Total_Benar"] = sum(row_scores.values())
                    matrix_data.append(row_scores)

                df_matrix = pd.DataFrame(matrix_data)
                analisis_rows = []

                for idx, q in enumerate(soal_master, 1):
                    q_text = q.get("pertanyaan", "") if isinstance(q, dict) else str(q)
                    kunci_idx = q.get("kunci", 0) if isinstance(q, dict) else 0
                    kunci_str = ['A', 'B', 'C', 'D'][kunci_idx] if 0 <= kunci_idx <= 3 else "A"

                    counts = [0, 0, 0, 0]
                    for sub in sub_list:
                        ans_list = sub.get("jawaban", [])
                        if idx - 1 < len(ans_list):
                            ans = ans_list[idx - 1]
                            if isinstance(ans, int) and 0 <= ans <= 3:
                                counts[ans] += 1

                    jml_benar = counts[kunci_idx]
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
                        "Kunci": kunci_str,
                        "Benar": f"{jml_benar}/{total_responden}",
                        "% Benar": f"{pct_benar}%",
                        "Kesukaran": kategori_kesukaran,
                        "Status Validitas (r)": validity_status,
                        "Distribusi Opsi (A | B | C | D)": f"A: {counts[0]} | B: {counts[1]} | C: {counts[2]} | D: {counts[3]}"
                    })

                st.dataframe(pd.DataFrame(analisis_rows), use_container_width=True)

        with t_kontrol:
            st.subheader("🔓 Kontrol Izin & Buka Kunci Siswa (Ulangan Harian)")
            if not is_ulangan_task:
                st.info("ℹ️ Fitur kontrol izin buka kunci hanya berlaku untuk tugas jenis **Ulangan Harian**.")
            else:
                locked_students = [
                    s for s in siswa_list 
                    if status_map.get(s["username"], {}).get("status") == "in_progress" 
                    and not status_map.get(s["username"], {}).get("ijin_guru", False)
                ]
                if not locked_students:
                    st.info("ℹ️ Tidak ada siswa yang saat ini terkunci.")
                else:
                    for ls in locked_students:
                        un_l = ls["username"]
                        nm_l = ls.get("nama", un_l)
                        st_l = status_map.get(un_l, {})
                        v_c = st_l.get("violation_count", 0)
                        
                        c_info, c_act = st.columns([3, 1])
                        c_info.write(f"👤 **{nm_l}** (@{un_l}) — Pelanggaran: **{v_c}x** | Status: **🔒 Terkunci (Keluar/Refresh)**")
                        if c_act.button("🔓 Beri Izin Mengerjakan", key=f"btn_grant_{un_l}"):
                            db.collection("status_ujian").document(f"{un_l}_{selected_tugas_id}").set({
                                "ijin_guru": True, "updated_at": firestore.SERVER_TIMESTAMP
                            }, merge=True)
                            st.success(f"✅ Izin berhasil diberikan kepada {nm_l}!")
                            st.rerun()

    elif menu == "📜 Daftar Nilai":
        st.header("📜 Transkrip & Daftar Nilai Siswa")
        if not pilihan_kelas:
            st.warning("⚠️ Anda belum ditugaskan mengajar kelas manapun.")
            st.stop()

        selected_kelas = st.selectbox("🏫 Pilih Kelas Ajar", options=pilihan_kelas, key="sb_dn_kelas")
        
        tugas_kelas = [d for d in get_all_tugas_cached() if is_target_sesuai_kelas(d, selected_kelas)]
        valid_tugas_ids = {tg["id"] for tg in tugas_kelas}
        
        siswa_docs = db.collection("users").where("role", "==", "siswa").where("kelas", "==", selected_kelas).stream()
        siswa_list = sorted([{"username": d.id, **d.to_dict()} for d in siswa_docs], key=lambda x: str(x.get("nama", "")).lower())

        if not siswa_list:
            st.info(f"Belum ada siswa terdaftar di Kelas **{selected_kelas}**.")
        elif not tugas_kelas:
            st.info(f"Belum ada tugas/kuis aktif untuk Kelas **{selected_kelas}**.")
        else:
            sub_docs = db.collection("jawaban_siswa").where("kelas_siswa", "==", selected_kelas).stream()
            sub_list = [d.to_dict() for d in sub_docs if d.to_dict().get("id_tugas") in valid_tugas_ids]
            
            sub_map = {(s.get("username_siswa"), s.get("id_tugas")): s for s in sub_list}

            table_rows = []
            for s in siswa_list:
                un = s["username"]
                row_data = {
                    "Username": un,
                    "Nama Siswa": s.get("nama", un)
                }

                numeric_scores = []
                for tg in tugas_kelas:
                    tg_id = tg["id"]
                    tg_title = tg.get("judul", tg_id)
                    sub = sub_map.get((un, tg_id))

                    if sub and sub.get("nilai") is not None:
                        val = sub.get("nilai")
                        row_data[tg_title] = val
                        try:
                            numeric_scores.append(float(val))
                        except (ValueError, TypeError):
                            pass
                    elif sub:
                        row_data[tg_title] = "Belum Dinilai"
                    else:
                        row_data[tg_title] = "-"

                row_data["Rata-Rata Nilai"] = round(sum(numeric_scores) / len(numeric_scores), 2) if numeric_scores else "-"
                table_rows.append(row_data)

            df_daftar_nilai = pd.DataFrame(table_rows)
            st.dataframe(df_daftar_nilai, use_container_width=True)

            csv_data = df_daftar_nilai.to_csv(index=False).encode('utf-8-sig')
            st.download_button(
                label=f"💾 Unduh Rekap Transkrip Nilai Kelas {selected_kelas} (.csv)",
                data=csv_data,
                file_name=f"rekap_nilai_kelas_{selected_kelas}.csv",
                mime="text/csv",
                use_container_width=True
            )

# ==========================================
# 8. PANEL SISWA (ULANGAN vs TUGAS BIASA)
# ==========================================
def render_siswa():
    kelas_s = user_info.get("kelas", "-")
    nama_s = user_info.get("nama", "Siswa")
    username_s = user_info.get("username", "")

    my_subs = get_user_submissions_cached(username_s)
    active_quiz_id = st.session_state.get("active_quiz_id")

    if active_quiz_id:
        if "active_quiz_data" not in st.session_state or st.session_state["active_quiz_data"]["id"] != active_quiz_id:
            all_t = get_all_tugas_cached()
            st.session_state["active_quiz_data"] = next((t for t in all_t if t["id"] == active_quiz_id), None)

        tg = st.session_state.get("active_quiz_data")
        if not tg:
            st.session_state["active_quiz_id"] = None
            st.rerun()

        tg_id = tg["id"]
        jenis_tugas = tg.get("jenis_tugas", "Ulangan Harian")
        is_ulangan = (jenis_tugas == "Ulangan Harian")

        if f"quiz_soal_{tg_id}" not in st.session_state:
            st.session_state[f"quiz_soal_{tg_id}"] = tg.get("soal", [])
        soal_list = st.session_state[f"quiz_soal_{tg_id}"]
        total_soal = len(soal_list)

        if f"quiz_loaded_{tg_id}" not in st.session_state:
            status_ref = db.collection("status_ujian").document(f"{username_s}_{tg_id}")
            status_doc = status_ref.get()
            status_data = status_doc.to_dict() if status_doc.exists else {}
            
            # Inisialisasi jawaban selalu kosong (Auto save draft dinonaktifkan)
            st.session_state[f"quiz_answers_{tg_id}"] = [None] * total_soal

            if is_ulangan:
                if not status_doc.exists:
                    status_ref.set({
                        "username": username_s, "id_tugas": tg_id, "status": "in_progress",
                        "ijin_guru": False, "violation_count": 0, "updated_at": firestore.SERVER_TIMESTAMP
                    }, merge=True)
                    st.session_state[f"ijin_guru_{tg_id}"] = True
                elif status_data.get("status") == "in_progress":
                    ijin_db = status_data.get("ijin_guru", False)
                    st.session_state[f"ijin_guru_{tg_id}"] = ijin_db
                    if ijin_db:
                        status_ref.set({"ijin_guru": False}, merge=True)
                else:
                    st.session_state[f"ijin_guru_{tg_id}"] = status_data.get("ijin_guru", False)
                st.session_state[f"violation_count_{tg_id}"] = status_data.get("violation_count", 0)
            else:
                st.session_state[f"ijin_guru_{tg_id}"] = True
                st.session_state[f"violation_count_{tg_id}"] = 0

            st.session_state[f"quiz_page_{tg_id}"] = 0
            st.session_state[f"quiz_loaded_{tg_id}"] = True

        answers = st.session_state[f"quiz_answers_{tg_id}"]
        curr_page = st.session_state[f"quiz_page_{tg_id}"]
        violation_count = st.session_state.get(f"violation_count_{tg_id}", 0)
        ijin_guru = st.session_state.get(f"ijin_guru_{tg_id}", True)
        
        terjawab_count = sum(1 for a in answers if a is not None and (not isinstance(a, str) or a.strip() != ""))
        is_locked = (not ijin_guru) if is_ulangan else False

        if is_ulangan:
            if st.button("⚠️ Catat Pelanggaran", key=f"btn_record_violation_{tg_id}", type="secondary"):
                if not is_locked:
                    doc_ref = db.collection("status_ujian").document(f"{username_s}_{tg_id}")
                    doc_ref.set({
                        "username": username_s, 
                        "id_tugas": tg_id, 
                        "violation_count": firestore.Increment(1),
                        "status": "in_progress", 
                        "updated_at": firestore.SERVER_TIMESTAMP
                    }, merge=True)
                    
                    st.session_state[f"violation_count_{tg_id}"] += 1
                    new_v = st.session_state[f"violation_count_{tg_id}"]

                    if new_v >= 15:
                        tg_sub = dict(tg)
                        tg_sub["soal"] = soal_list
                        submit_jawaban_siswa(tg_sub, username_s, nama_s, kelas_s, answers, is_violation=True)
                        
                        st.session_state["active_quiz_id"] = None
                        for k in [f"active_quiz_data", f"quiz_answers_{tg_id}", f"quiz_page_{tg_id}", f"quiz_soal_{tg_id}", f"quiz_loaded_{tg_id}"]:
                            st.session_state.pop(k, None)
                        st.error("🚨 Kuis di-submit otomatis karena mencapai 15 kali pelanggaran!")
                        st.rerun()
                    else:
                        st.rerun()

            if not is_locked:
                components.html("""
                    <script>
                    (function() {
                        const parentDoc = window.parent.document;
                        let lastTrigger = 0;

                        function getViolationButton() {
                            const buttons = Array.from(parentDoc.querySelectorAll('button'));
                            return buttons.find(b => b.innerText.includes('Catat Pelanggaran'));
                        }

                        function hideViolationButton() {
                            const btn = getViolationButton();
                            if (btn && btn.parentElement) {
                                const container = btn.closest('[data-testid="stElementContainer"]') || btn.parentElement;
                                if (container && container.style.display !== 'none') {
                                    container.style.display = 'none';
                                }
                            }
                        }

                        setInterval(hideViolationButton, 500);

                        function triggerViolation() {
                            const now = Date.now();
                            if (now - lastTrigger < 3000) return;
                            lastTrigger = now;

                            const triggerBtn = getViolationButton();
                            if (triggerBtn) {
                                triggerBtn.click();
                            }
                        }

                        parentDoc.addEventListener('visibilitychange', function() {
                            if (parentDoc.hidden) triggerViolation();
                        });

                        window.parent.addEventListener('blur', function() {
                            triggerViolation();
                        });
                    })();
                    </script>
                """, height=0)

        if is_locked:
            st.error("🔒 **ULANGAN HARIAN TERKUNCI**: Anda terdeteksi **keluar/ke-refresh** dari kuis atau mencapai batas pelanggaran. Anda wajib meminta izin kepada Guru untuk dapat melanjutkan pengerjaan.")
            if st.button("🔄 Cek Status Izin Guru", key=f"btn_check_permission_{tg_id}", type="primary"):
                status_ref = db.collection("status_ujian").document(f"{username_s}_{tg_id}")
                status_doc = status_ref.get()
                if status_doc.exists:
                    ijin_val = status_doc.to_dict().get("ijin_guru", False)
                    st.session_state[f"ijin_guru_{tg_id}"] = ijin_val
                    if ijin_val:
                        status_ref.set({"ijin_guru": False}, merge=True)
                st.rerun()

        elif is_ulangan and violation_count >= 5:
            st.warning(f"⚠️ **PERINGATAN PELANGGARAN ({violation_count}/15)**: Terdeteksi keluar dari layar kuis!")

        col_head1, col_head2 = st.columns([3, 1])
        with col_head1:
            st.markdown(f"### 📝 {tg.get('judul')}")
            info_sub = f"Tipe: **{tg.get('tipe', 'pg').upper()}** | Jenis: **{jenis_tugas}** | Terjawab: **{terjawab_count}/{total_soal}**"
            if is_ulangan: info_sub += f" | Pelanggaran: **{violation_count}x**"
            st.caption(info_sub)
        with col_head2:
            if st.button("⬅️ Keluar Sementara", key="btn_exit_quiz", type="secondary", use_container_width=True):
                if is_ulangan:
                    db.collection("status_ujian").document(f"{username_s}_{tg_id}").set({"ijin_guru": False}, merge=True)
                st.session_state["active_quiz_id"] = None
                st.session_state.pop("active_quiz_data", None)
                st.session_state.pop(f"quiz_loaded_{tg_id}", None)
                st.rerun()

        st.progress((curr_page + 1) / total_soal)

        c_top_prev, c_top_next = st.columns(2)
        with c_top_prev:
            if curr_page > 0 and st.button("⬅️ Sebelumnya", key=f"top_prev_{tg_id}", use_container_width=True, disabled=is_locked):
                st.session_state[f"quiz_page_{tg_id}"] -= 1
                st.rerun()
        with c_top_next:
            if curr_page < total_soal - 1 and st.button("Selanjutnya ➡️", key=f"top_next_{tg_id}", type="primary", use_container_width=True, disabled=is_locked):
                st.session_state[f"quiz_page_{tg_id}"] += 1
                st.rerun()

        st.divider()

        soal_item = soal_list[curr_page]
        q_text = soal_item.get("pertanyaan") if isinstance(soal_item, dict) else str(soal_item)

        with st.container(border=True):
            st.markdown(f"#### Soal No. {curr_page + 1} dari {total_soal}")
            st.markdown(f"**{q_text}**")

            if tg.get("tipe") == "pg":
                opsi_list = soal_item.get("opsi", [])
                saved_ans = answers[curr_page]
                selected_opt = st.radio(
                    "Pilih Jawaban Anda:", options=[0, 1, 2, 3],
                    index=saved_ans if saved_ans is not None else None,
                    format_func=lambda x: f"{['A','B','C','D'][x]}. {opsi_list[x]}",
                    key=f"radio_q_{tg_id}_{curr_page}",
                    disabled=is_locked
                )
                if not is_locked and selected_opt != saved_ans:
                    answers[curr_page] = selected_opt
                    st.session_state[f"quiz_answers_{tg_id}"] = answers
            else:
                saved_text = answers[curr_page] or ""
                essay_text = st.text_area(
                    "Jawaban Anda:", value=saved_text, height=140, key=f"essay_q_{tg_id}_{curr_page}",
                    disabled=is_locked
                )
                if not is_locked and essay_text != saved_text:
                    answers[curr_page] = essay_text if essay_text.strip() else None
                    st.session_state[f"quiz_answers_{tg_id}"] = answers

        cols_per_row = 5
        for row_start in range(0, total_soal, cols_per_row):
            nav_cols = st.columns(cols_per_row)
            for idx in range(cols_per_row):
                q_idx = row_start + idx
                if q_idx < total_soal:
                    is_ans = answers[q_idx] is not None
                    lbl = f"{'🟢' if is_ans else '⚪'} {q_idx + 1}"
                    btn_t = "primary" if q_idx == curr_page else "secondary"
                    if nav_cols[idx].button(lbl, key=f"nav_p_{q_idx}", type=btn_t, use_container_width=True, disabled=is_locked):
                        st.session_state[f"quiz_page_{tg_id}"] = q_idx
                        st.rerun()

        st.divider()

        if not is_locked:
            if st.button("🚀 Kumpulkan Semua Jawaban", key=f"bot_submit_{tg_id}", type="primary", use_container_width=True):
                tg_submit = dict(tg)
                tg_submit["soal"] = soal_list
                
                with st.spinner("Memproses pengumpulan jawaban..."):
                    success = submit_jawaban_siswa(tg_submit, username_s, nama_s, kelas_s, answers, is_forced=False)
                
                if success:
                    st.balloons()
                    st.success("✅ Jawaban Anda berhasil dikumpulkan!")

                    st.session_state["active_quiz_id"] = None
                    for k in [f"active_quiz_data", f"quiz_answers_{tg_id}", f"quiz_page_{tg_id}", f"quiz_soal_{tg_id}", f"quiz_loaded_{tg_id}"]:
                        st.session_state.pop(k, None)
                    st.rerun()

        return

    all_tugas = [
        t for t in get_all_tugas_cached() 
        if is_target_sesuai_kelas(t, kelas_s) and t.get("status", "terbit") == "terbit"
    ]
    
    existing_tugas_dict = {t["id"]: t for t in all_tugas}

    st.markdown(f"""
        <div class="student-header">
            <div style="font-size: 0.85rem; opacity: 0.9;">🏫 Kelas {kelas_s}</div>
            <div style="font-size: 1.4rem; font-weight: bold;">Halo, {nama_s}! 👋</div>
        </div>
    """, unsafe_allow_html=True)

    tab_tugas, tab_materi, tab_nilai = st.tabs(["✍️ Tugas Saya", "📚 Modul Materi", "📊 Riwayat Nilai"])

    with tab_tugas:
        tugas_belum_list = [t for t in all_tugas if t["id"] not in my_subs]
        tugas_sudah_list = [t for t in all_tugas if t["id"] in my_subs]

        st.subheader("🔴 Tugas Belum Dikerjakan")
        if not tugas_belum_list:
            st.success("✨ Semua tugas telah dikumpulkan!")
        else:
            for tg in tugas_belum_list:
                with st.container(border=True):
                    jenis_t = tg.get("jenis_tugas", "Ulangan Harian")
                    tag_color = "🔴 Ulangan Harian" if jenis_t == "Ulangan Harian" else "🟢 Tugas Biasa"
                    
                    st.markdown(f"### 📝 {tg.get('judul')} [{tag_color}]")
                    st.caption(f"Tipe: **{tg.get('tipe', 'pg').upper()}** | {len(tg.get('soal', []))} Soal")
                    
                    if st.button("🚀 Mulai Kerjakan", key=f"start_{tg['id']}", type="primary"):
                        st.session_state["active_quiz_id"] = tg["id"]
                        st.rerun()

        if tugas_sudah_list:
            st.divider()
            st.subheader("🟢 Tugas Sudah Dikerjakan")
            for tg in tugas_sudah_list:
                sub_data = my_subs.get(tg["id"], {})
                val = sub_data.get("nilai")
                catatan = sub_data.get("catatan_guru", "")
                st.write(f"- **{tg.get('judul')}** | Nilai: **{val if val is not None else 'Menunggu Koreksi'}** {f'({catatan})' if catatan else ''}")

    with tab_materi:
        materi_docs = [m for m in get_all_materi_cached() if is_target_sesuai_kelas(m, kelas_s)]
        if not materi_docs:
            st.info("Belum ada materi untuk kelas Anda.")
        else:
            for m in materi_docs:
                with st.container(border=True):
                    st.markdown(f"#### 📘 [{m.get('bab')}] {m.get('judul')}")
                    if m.get("konten"): st.write(m.get("konten"))
                    if m.get("file_url"): st.link_button("📎 Buka Dokumen", m.get("file_url"))

    with tab_nilai:
        st.subheader("📊 Riwayat Nilai & Feedback")
        
        valid_subs = {tg_id: sub for tg_id, sub in my_subs.items() if tg_id in existing_tugas_dict}

        if not valid_subs:
            st.info("Belum ada riwayat nilai untuk tugas yang aktif.")
        else:
            riwayat_rows = []
            for tg_id, sub_info in valid_subs.items():
                tg_obj = existing_tugas_dict.get(tg_id, {})
                val = sub_info.get("nilai")
                riwayat_rows.append({
                    "Judul Tugas": tg_obj.get("judul", sub_info.get("judul_tugas", "-")),
                    "Tipe Soal": sub_info.get("tipe", "-").upper(),
                    "Nilai": val if val is not None else "Menunggu Koreksi",
                    "Catatan Guru": sub_info.get("catatan_guru", "-")
                })
            
            st.dataframe(pd.DataFrame(riwayat_rows), use_container_width=True)

# ==========================================
# 9. MAIN ROUTER
# ==========================================
if role == "superadmin":
    render_superadmin()
elif role == "guru":
    render_guru()
elif role == "siswa":
    render_siswa()
