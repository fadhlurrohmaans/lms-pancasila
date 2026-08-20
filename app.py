import streamlit as st
import firebase_admin
from firebase_admin import credentials, firestore
import pandas as pd
import hashlib
import io
import re
import random
import string
import json
import google.generativeai as genai

# ==========================================
# 1. CONFIG & CSS MOBILE RESPONSIVE
# ==========================================
st.set_page_config(
    page_title="LMS Pendidikan Pancasila",
    page_icon="🇮🇩",
    layout="wide",
    initial_sidebar_state="auto"
)

st.markdown("""
    <style>
    input[type="text"], input[type="password"], textarea, select {
        font-size: 16px !important;
    }
    
    @media (max-width: 768px) {
        .main .block-container {
            padding-left: 0.8rem !important;
            padding-right: 0.8rem !important;
            padding-top: 1rem !important;
            padding-bottom: 2rem !important;
        }
        
        [data-testid="column"] {
            width: 100% !important;
            flex: 1 1 100% !important;
            min-width: 100% !important;
            margin-bottom: 0.5rem;
        }

        .stButton > button, .stDownloadButton > button {
            width: 100% !important;
            min-height: 48px !important;
            font-size: 16px !important;
            font-weight: bold;
            border-radius: 8px !important;
            margin-top: 4px;
            margin-bottom: 4px;
        }
        
        h1 { font-size: 1.8rem !important; }
        h2 { font-size: 1.4rem !important; }
        h3 { font-size: 1.2rem !important; }
    }

    .stTabs [data-baseweb="tab-list"] {
        gap: 8px;
        overflow-x: auto;
        white-space: nowrap;
    }
    .stTabs [data-baseweb="tab"] {
        padding: 8px 16px;
    }
    </style>
""", unsafe_allow_html=True)

# ==========================================
# 2. INISIALISASI FIREBASE & HELPER
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

def safe_read_uploaded_file(uploaded_file):
    """Membaca file CSV/Excel dengan penanganan otomatis berbagai jenis encoding."""
    if uploaded_file.name.endswith('.csv'):
        encodings = ['utf-8', 'utf-8-sig', 'cp1252', 'latin1', 'iso-8859-1']
        for enc in encodings:
            try:
                uploaded_file.seek(0)
                return pd.read_csv(uploaded_file, encoding=enc)
            except (UnicodeDecodeError, UnicodeError):
                continue
        uploaded_file.seek(0)
        return pd.read_csv(uploaded_file, encoding='utf-8', errors='replace')
    else:
        return pd.read_excel(uploaded_file)

def hash_pass(password):
    return hashlib.sha256(password.encode()).hexdigest()

def generate_username(nama):
    base_username = re.sub(r'[^a-z0-9]', '', nama.lower())
    if not base_username:
        base_username = "siswa"
    
    username = base_username
    counter = 1
    while db.collection("users").document(username).get().exists:
        username = f"{base_username}{counter}"
        counter += 1
    return username

def generate_password(length=6):
    chars = string.ascii_lowercase + string.digits
    return ''.join(random.choice(chars) for _ in range(length))

def get_all_kelas():
    docs = db.collection("kelas").stream()
    return sorted([d.id for d in docs])

def is_tugas_sesuai_kelas(tugas_doc, kelas_siswa):
    target_kelas = tugas_doc.get("target_kelas", [])
    if not target_kelas:
        return True
    if isinstance(target_kelas, list):
        return kelas_siswa in target_kelas
    return target_kelas == kelas_siswa

# ==========================================
# 3. HELPER AI KOREKSI ESSAY AUTOMATIS
# ==========================================
def koreksi_essay_dengan_ai(soal_list, jawaban_list):
    api_key = (
        st.secrets.get("GEMINI_API_KEY") or 
        st.secrets.get("gemini", {}).get("api_key") or
        st.secrets.get("firebase", {}).get("GEMINI_API_KEY")
    )
    
    if not api_key:
        return None, "⚠️ Key 'GEMINI_API_KEY' belum dikonfigurasi di Streamlit Secrets."

    try:
        genai.configure(api_key=api_key)

        available_models = []
        try:
            for m in genai.list_models():
                if 'generateContent' in m.supported_generation_methods:
                    name = m.name.replace('models/', '')
                    available_models.append(name)
        except Exception:
            pass

        if not available_models:
            available_models = ['gemini-2.5-flash', 'gemini-2.0-flash', 'gemini-1.5-flash', 'gemini-1.5-pro']

        available_models.sort(key=lambda x: 0 if 'flash' in x else 1)

        prompt_soal_jawab = []
        for idx, (s, j) in enumerate(zip(soal_list, jawaban_list), 1):
            p_text = s.get("pertanyaan", "") if isinstance(s, dict) else str(s)
            j_text = j if j and str(j).strip() else "(Siswa tidak mengisi jawaban / kosong)"
            
            prompt_soal_jawab.append(
                f"Soal No.{idx}: {p_text}\n"
                f"Jawaban Siswa No.{idx}: {j_text}\n"
            )

        prompt = f"""
        Anda adalah seorang Guru dan Pakar Pendidikan Pancasila yang objektif, bijaksana, dan suportif.
        
        TUGAS ANDA:
        1. Baca dan pahami pertanyaan essay serta jawaban siswa di bawah ini.
        2. Gunakan pemahaman Anda tentang konsep Pendidikan Pancasila, Kewarganegaraan, UUD 1945, serta referensi jawaban ideal yang berlaku dalam Kurikulum Nasional Indonesia.
        3. Bandingkan jawaban siswa dengan konsep ideal tersebut secara kritis namun fair.

        --- PERTANYAAN DAN JAWABAN SISWA ---
        {chr(10).join(prompt_soal_jawab)}

        --- INSTRUKSI EVALUASI ---
        1. Berikan nilai akumulatif dari skala 0 hingga 100 (berupa angka bulat).
        2. Berikan feedback/catatan perbaikan ringkas yang konstruktif dan memotivasi siswa (maksimal 3-4 kalimat).
        3. Output WAJIB dalam format JSON murni:
        {{
            "nilai": 85,
            "feedback": "Penjelasan konsep Sila ke-3 sudah sangat baik dan sesuai dengan contoh sehari-hari."
        }}
        """

        response = None
        last_error = None

        for m_name in available_models:
            try:
                model = genai.GenerativeModel(m_name)
                response = model.generate_content(
                    prompt,
                    generation_config={"response_mime_type": "application/json"}
                )
                if response and response.text:
                    break
            except Exception as e:
                last_error = e
                continue

        if not response:
            return None, f"Gagal mengeksekusi AI. Model yang dicoba: {available_models}. Error terakhir: {last_error}"

        result_json = json.loads(response.text)
        nilai_ai = int(result_json.get("nilai", 0))
        feedback_ai = str(result_json.get("feedback", ""))
        
        return nilai_ai, feedback_ai

    except Exception as e:
        return None, f"Gagal mengeksekusi AI: {e}"

# ==========================================
# 4. HALAMAN LOGIN
# ==========================================
if "user" not in st.session_state:
    st.session_state["user"] = None

if st.session_state.get("user") is None:
    st.title("🇮🇩 LMS Pendidikan Pancasila")
    st.subheader("Silakan Login untuk Mengakses Sistem")
    
    with st.container():
        st.info("💡 **Informasi**: Akun Siswa dan Guru dibuat serta dikelola secara resmi oleh **Super Admin**.")
        
        with st.form("form_login"):
            username = st.text_input("Username").strip().lower()
            password = st.text_input("Password", type="password")
            btn_login = st.form_submit_button("Masuk / Login")

            if btn_login:
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
                        else:
                            st.error("Password salah!")
                    else:
                        st.error("Username tidak terdaftar! Hubungi Super Admin.")
                else:
                    st.warning("Mohon isi username dan password.")

    st.stop()

# ==========================================
# 5. HEADER & SIDEBAR USER
# ==========================================
user_info = st.session_state["user"]
role = user_info["role"]

st.sidebar.title(f"👋 Halo, {user_info['nama']}")

caption_text = f"Role: **{role.upper()}** | @{user_info['username']}"
if role == "siswa" and user_info.get("kelas"):
    caption_text += f"\n\n🏫 Kelas: **{user_info['kelas']}**"
elif role == "guru" and user_info.get("kelas_ajar"):
    k_str = ", ".join(user_info['kelas_ajar']) if isinstance(user_info['kelas_ajar'], list) else user_info['kelas_ajar']
    caption_text += f"\n\n🏫 Mengajar Kelas: **{k_str}**"

st.sidebar.caption(caption_text)

if st.sidebar.button("🚪 Keluar / Logout"):
    st.session_state["user"] = None
    st.rerun()

st.sidebar.divider()

# ==========================================
# 6. PANEL SUPER ADMIN
# ==========================================
if role == "superadmin":
    st.title("⚙️ Panel Super Admin")
    tab_master_kelas, tab_list_user, tab_add_user, tab_import_export, tab_edit_kelas, tab_del_user = st.tabs([
        "🏫 Master Kelas",
        "👥 Daftar User", 
        "➕ Buat Akun",
        "📥 Import & Ekspor",
        "✏️ Atur Kelas",
        "🗑️ Hapus Akun"
    ])

    # --- 6A. MASTER DATA KELAS ---
    with tab_master_kelas:
        st.subheader("🏫 Kelola Master Data Kelas")
        st.caption("Tambahkan daftar kelas resmi sekolah di sini sebelum membuat akun Guru / Siswa.")
        
        col_k1, col_k2 = st.columns([1, 1])
        daftar_kelas_aktif = get_all_kelas()

        with col_k1:
            st.write("📋 **Daftar Kelas Terdaftar:**")
            if daftar_kelas_aktif:
                for k in daftar_kelas_aktif:
                    st.markdown(f"- 🏫 **{k}**")
            else:
                st.info("Belum ada data kelas. Silakan tambahkan kelas baru.")

        with col_k2:
            st.write("➕ **Tambah Kelas Baru**")
            with st.form("form_add_kelas", clear_on_submit=True):
                new_kelas_name = st.text_input("Nama Kelas Baru", placeholder="Contoh: X IPA 1").strip()
                btn_add_k = st.form_submit_button("Tambah Kelas")
                
                if btn_add_k:
                    if new_kelas_name:
                        db.collection("kelas").document(new_kelas_name).set({
                            "nama": new_kelas_name,
                            "created_at": firestore.SERVER_TIMESTAMP
                        })
                        st.success(f"✅ Berhasil! Kelas '{new_kelas_name}' telah ditambahkan ke sistem.")
                        st.rerun()
                    else:
                        st.warning("Nama kelas tidak boleh kosong!")

            if daftar_kelas_aktif:
                st.divider()
                st.write("🗑️ **Hapus Kelas**")
                del_k_name = st.selectbox("Pilih Kelas yang Ingin Dihapus", daftar_kelas_aktif)
                if st.button("Hapus Kelas Ini", type="primary"):
                    db.collection("kelas").document(del_k_name).delete()
                    st.success(f"✅ Berhasil! Kelas '{del_k_name}' telah dihapus.")
                    st.rerun()

    # --- 6B. LIST USERS ---
    with tab_list_user:
        st.subheader("👥 Daftar Akun Terdaftar")
        docs = db.collection("users").stream()
        users_list = []
        for d in docs:
            u = d.to_dict()
            u_role = u.get("role", "").lower()
            
            info_kelas = "-"
            if u_role == "siswa":
                info_kelas = u.get("kelas", "-")
            elif u_role == "guru":
                k_list = u.get("kelas_ajar", [])
                info_kelas = ", ".join(k_list) if isinstance(k_list, list) and k_list else (k_list if k_list else "-")

            users_list.append({
                "Username": d.id,
                "Nama Lengkap": u.get("nama"),
                "Role": u_role.upper(),
                "Kelas / Kelas Ajar": info_kelas
            })
        if users_list:
            st.dataframe(pd.DataFrame(users_list), use_container_width=True)
        else:
            st.info("Belum ada data pengguna.")

    # --- 6C. ADD USER MANUAL ---
    with tab_add_user:
        st.subheader("➕ Buat Akun Satuan (Manual)")
        
        daftar_kelas_pilihan = get_all_kelas()
        new_role = st.selectbox("Role Akun", ["Siswa", "Guru", "Superadmin"])
        
        with st.form("form_create_user", clear_on_submit=True):
            new_nama = st.text_input("Nama Lengkap")
            new_username = st.text_input("Username Baru").strip().lower()
            new_password = st.text_input("Password", type="password")
            
            kelas_siswa_selected = ""
            kelas_guru_selected = []
            
            if new_role == "Siswa":
                if daftar_kelas_pilihan:
                    kelas_siswa_selected = st.selectbox("Pilih Kelas Siswa", options=daftar_kelas_pilihan)
                else:
                    st.warning("⚠️ Master data kelas masih kosong! Buat kelas terlebih dahulu.")
            
            elif new_role == "Guru":
                if daftar_kelas_pilihan:
                    kelas_guru_selected = st.multiselect("Pilih Kelas yang Diajar Guru", options=daftar_kelas_pilihan)
                else:
                    st.warning("⚠️ Master data kelas masih kosong! Buat kelas terlebih dahulu.")

            btn_create = st.form_submit_button("Buat Akun Baru")

            if btn_create:
                if new_nama and new_username and new_password:
                    if new_role in ["Siswa", "Guru"] and not daftar_kelas_pilihan:
                        st.error("Gagal membuat akun. Silakan tambahkan kelas di tab 'Master Kelas' terlebih dahulu.")
                    else:
                        u_check = db.collection("users").document(new_username).get()
                        if u_check.exists:
                            st.error("Username sudah digunakan. Silakan pakai username lain.")
                        else:
                            data_user = {
                                "nama": new_nama,
                                "password": hash_pass(new_password),
                                "password_plain": new_password,
                                "role": new_role.lower(),
                                "created_at": firestore.SERVER_TIMESTAMP
                            }
                            
                            if new_role == "Siswa":
                                data_user["kelas"] = kelas_siswa_selected
                            elif new_role == "Guru":
                                data_user["kelas_ajar"] = kelas_guru_selected

                            db.collection("users").document(new_username).set(data_user)
                            st.success(f"✅ Berhasil! Akun {new_role} '{new_username}' telah dibuat.")
                            st.rerun()
                else:
                    st.warning("Mohon lengkapi seluruh isian formulir!")

    # --- 6D. IMPORT & EKSPOR DATA SISWA ---
    with tab_import_export:
        st.subheader("📥 Import & 📤 Ekspor Data Siswa")
        col_imp, col_exp = st.columns([1, 1])

        with col_imp:
            st.markdown("### 📥 Import Data Siswa Massal")
            st.caption("• Nama Baru: Dibuatkan **username & password otomatis**.\n• Nama Sudah Ada: **Hanya kelasnya saja yang diperbarui**.")
            
            df_template = pd.DataFrame([
                {"nama": "Budi Santoso", "kelas": "X IPA 1"},
                {"nama": "Siti Aminah", "kelas": "X IPA 2"},
                {"nama": "Ahmad Dani", "kelas": "X IPA 1"}
            ])
            csv_template = df_template.to_csv(index=False).encode('utf-8')
            
            st.download_button(
                label="📄 Download Template File (CSV)",
                data=csv_template,
                file_name="template_import_siswa.csv",
                mime="text/csv"
            )

            st.divider()

            uploaded_file = st.file_uploader("Unggah File Siswa (.csv atau .xlsx)", type=["csv", "xlsx"])
            if uploaded_file is not None:
                try:
                    df_import = safe_read_uploaded_file(uploaded_file)
                    df_import.columns = [str(col).strip().lower() for col in df_import.columns]
                    req_cols = ["nama", "kelas"]

                    if not all(col in df_import.columns for col in req_cols):
                        st.error(f"Format file tidak valid! File wajib memiliki kolom: **{', '.join(req_cols)}**")
                    else:
                        st.write("👀 **Pratinjau Data yang Akan Diimpor:**")
                        st.dataframe(df_import, use_container_width=True)

                        if st.button("🚀 Mulai Import Data Siswa", type="primary"):
                            created_count = 0
                            updated_count = 0
                            skipped_count = 0

                            docs_siswa = db.collection("users").where("role", "==", "siswa").stream()
                            existing_siswa_map = {}
                            for doc in docs_siswa:
                                u_data = doc.to_dict()
                                nama_val = u_data.get("nama", "").strip().lower()
                                if nama_val:
                                    existing_siswa_map[nama_val] = doc.id

                            for idx, row in df_import.iterrows():
                                nama_s = str(row["nama"]).strip()
                                kelas_s = str(row["kelas"]).strip()

                                if not nama_s or pd.isna(row["nama"]):
                                    skipped_count += 1
                                    continue

                                nama_key = nama_s.lower()

                                if nama_key in existing_siswa_map:
                                    target_username = existing_siswa_map[nama_key]
                                    db.collection("users").document(target_username).update({
                                        "kelas": kelas_s
                                    })
                                    updated_count += 1
                                else:
                                    u_name = generate_username(nama_s)
                                    p_plain = generate_password(6)
                                    p_hashed = hash_pass(p_plain)

                                    db.collection("users").document(u_name).set({
                                        "nama": nama_s,
                                        "password": p_hashed,
                                        "password_plain": p_plain,
                                        "role": "siswa",
                                        "kelas": kelas_s,
                                        "created_at": firestore.SERVER_TIMESTAMP
                                    })
                                    existing_siswa_map[nama_key] = u_name
                                    created_count += 1

                            st.success(f"✅ Berhasil! Import Data Selesai.\n- **{created_count}** akun siswa baru ditambahkan.\n- **{updated_count}** data siswa diperbarui kelasnya.")
                            if skipped_count > 0:
                                st.warning(f"⚠️ **{skipped_count}** baris dilewati karena kolom nama kosong.")
                            st.rerun()

                except Exception as e:
                    st.error(f"Gagal memproses file: {e}")

        with col_exp:
            st.markdown("### 📤 Ekspor Data Siswa Lengkap")
            st.caption("Menampilkan data lengkap: Nama, Username, Password, dan Kelas.")

            docs_siswa = db.collection("users").where("role", "==", "siswa").stream()
            data_siswa = []
            for d in docs_siswa:
                u_dict = d.to_dict()
                data_siswa.append({
                    "Nama Lengkap": u_dict.get("nama", ""),
                    "Username": d.id,
                    "Password": u_dict.get("password_plain", "*****"),
                    "Kelas": u_dict.get("kelas", "")
                })

            if data_siswa:
                df_export = pd.DataFrame(data_siswa)
                st.write(f"📊 Total Siswa Terdaftar: **{len(data_siswa)}**")
                st.dataframe(df_export, use_container_width=True)

                csv_data = df_export.to_csv(index=False).encode('utf-8')
                st.download_button(
                    label="💾 Unduh Data Lengkap (CSV)",
                    data=csv_data,
                    file_name="daftar_siswa_lengkap.csv",
                    mime="text/csv"
                )

                try:
                    buffer = io.BytesIO()
                    with pd.ExcelWriter(buffer, engine='openpyxl') as writer:
                        df_export.to_excel(writer, index=False, sheet_name='Data Siswa')
                    
                    st.download_button(
                        label="📊 Unduh Data Lengkap (Excel .xlsx)",
                        data=buffer.getvalue(),
                        file_name="daftar_siswa_lengkap.xlsx",
                        mime="application/vnd.openxmlformats-officedocument.spreadsheetml.sheet"
                    )
                except ModuleNotFoundError:
                    st.info("💡 Tambahkan `openpyxl` ke file `requirements.txt` jika ingin mengunduh format Excel (`.xlsx`).")
            else:
                st.info("Belum ada data siswa untuk diekspor.")

    # --- 6E. EDIT KELAS USER ---
    with tab_edit_kelas:
        st.subheader("✏️ Atur / Perbarui Kelas Guru & Siswa")
        docs = db.collection("users").stream()
        daftar_kelas_pilihan = get_all_kelas()
        all_non_admin = {}
        
        for d in docs:
            u = d.to_dict()
            if u.get("role") in ["siswa", "guru"]:
                all_non_admin[d.id] = f"{u.get('nama')} (@{d.id}) - [{u.get('role').upper()}]"

        if not daftar_kelas_pilihan:
            st.warning("⚠️ Belum ada master data kelas! Silakan tambahkan data kelas di tab 'Master Kelas'.")
        elif all_non_admin:
            selected_user_id = st.selectbox(
                "Pilih Akun yang Ingin Diatur Kelasnya", 
                list(all_non_admin.keys()), 
                format_func=lambda x: all_non_admin[x]
            )
            
            u_doc = db.collection("users").document(selected_user_id).get().to_dict()
            u_role = u_doc.get("role")
            
            with st.form("form_edit_kelas"):
                st.write(f"**Nama:** {u_doc.get('nama')}")
                st.write(f"**Role:** {u_role.upper()}")
                
                if u_role == "siswa":
                    kelas_lama = u_doc.get("kelas", "")
                    idx_default = daftar_kelas_pilihan.index(kelas_lama) if kelas_lama in daftar_kelas_pilihan else 0
                    
                    new_kelas_siswa = st.selectbox("Pilih Kelas Siswa", options=daftar_kelas_pilihan, index=idx_default)
                    btn_save_kelas = st.form_submit_button("Simpan Perubahan Kelas")
                    
                    if btn_save_kelas:
                        db.collection("users").document(selected_user_id).update({
                            "kelas": new_kelas_siswa
                        })
                        st.success(f"✅ Berhasil! Kelas untuk siswa {u_doc.get('nama')} telah diperbarui.")
                        st.rerun()

                elif u_role == "guru":
                    kelas_ajar_lama = u_doc.get("kelas_ajar", [])
                    default_g = [k for k in kelas_ajar_lama if k in daftar_kelas_pilihan] if isinstance(kelas_ajar_lama, list) else []
                    
                    new_kelas_guru_list = st.multiselect("Pilih Kelas Ajar Guru", options=daftar_kelas_pilihan, default=default_g)
                    btn_save_kelas = st.form_submit_button("Simpan Perubahan Kelas Ajar")
                    
                    if btn_save_kelas:
                        db.collection("users").document(selected_user_id).update({
                            "kelas_ajar": new_kelas_guru_list
                        })
                        st.success(f"✅ Berhasil! Daftar kelas ajar Guru {u_doc.get('nama')} telah diperbarui.")
                        st.rerun()
        else:
            st.info("Belum ada akun Guru atau Siswa yang terdaftar.")

    # --- 6F. DELETE USER ---
    with tab_del_user:
        st.subheader("🗑️ Hapus Akun Pengguna")
        docs = db.collection("users").stream()
        all_users = {d.id: f"{d.to_dict().get('nama')} (@{d.id}) - [{d.to_dict().get('role').upper()}]" for d in docs}
        all_users_filtered = {k: v for k, v in all_users.items() if k != user_info["username"]}

        if all_users_filtered:
            selected_del = st.selectbox("Pilih Akun yang Ingin Dihapus", list(all_users_filtered.keys()), format_func=lambda x: all_users_filtered[x])
            if st.button("Hapus Akun Ini", type="primary"):
                db.collection("users").document(selected_del).delete()
                st.success("✅ Berhasil! Akun telah dihapus dari sistem.")
                st.rerun()
        else:
            st.info("Tidak ada akun lain yang dapat dihapus.")

# ==========================================
# 7. PANEL GURU
# ==========================================
elif role == "guru":
    st.title("🇮🇩 Panel Guru")
    menu = st.sidebar.radio(
        "📌 Menu Guru",
        ["📖 Kelola Materi", "📝 Buat & Kelola Tugas", "📊 Rekap & Periksa Nilai"]
    )

    master_kelas_all = get_all_kelas()
    guru_kelas_ajar = user_info.get("kelas_ajar", [])
    if isinstance(guru_kelas_ajar, str):
        guru_kelas_ajar = [guru_kelas_ajar]
    
    pilihan_kelas_tugas = guru_kelas_ajar if guru_kelas_ajar else master_kelas_all

    # --- 7A. KELOLA MATERI ---
    if menu == "📖 Kelola Materi":
        st.header("📖 Kelola Materi Pembelajaran")
        t1, t2, t3 = st.tabs(["📋 Daftar Materi", "➕ Tambah Materi", "🗑️ Hapus Materi"])

        with t1:
            docs = db.collection("materi_pancasila").stream()
            materi_list = [{"id": d.id, **d.to_dict()} for d in docs]
            if materi_list:
                for item in materi_list:
                    with st.expander(f"📌 [{item.get('bab')}] {item.get('judul')}"):
                        st.write(item.get("konten"))
            else:
                st.info("Belum ada materi pembelajaran.")

        with t2:
            with st.form("form_materi"):
                bab = st.text_input("Bab / Topik", placeholder="Misal: Bab 1 - Perumusan Pancasila")
                judul = st.text_input("Judul Materi", placeholder="Misal: Peran Tokoh Bangsa")
                konten = st.text_area("Isi Penjelasan Materi", height=200)
                sub = st.form_submit_button("Simpan Materi")
                if sub and bab and judul and konten:
                    db.collection("materi_pancasila").add({
                        "bab": bab,
                        "judul": judul,
                        "konten": konten,
                        "created_by": user_info["username"],
                        "created_at": firestore.SERVER_TIMESTAMP
                    })
                    st.success("✅ Berhasil! Materi pembelajaran baru telah disimpan.")
                    st.rerun()

        with t3:
            docs = db.collection("materi_pancasila").stream()
            materi_list = [{"id": d.id, **d.to_dict()} for d in docs]
            if materi_list:
                m_map = {m["id"]: f"[{m.get('bab')}] {m.get('judul')}" for m in materi_list}
                sel_m = st.selectbox("Pilih Materi untuk Dihapus", list(m_map.keys()), format_func=lambda x: m_map[x])
                if st.button("Hapus Materi", type="primary"):
                    db.collection("materi_pancasila").document(sel_m).delete()
                    st.success("✅ Berhasil! Materi telah dihapus.")
                    st.rerun()

    # --- 7B. BUAT & KELOLA TUGAS ---
    elif menu == "📝 Buat & Kelola Tugas":
        st.header("📝 Buat & Kelola Tugas")
        t_list, t_buat, t_edit, t_imp_exp = st.tabs([
            "📋 Daftar Tugas", 
            "➕ Buat Tugas", 
            "✏️ Edit Tugas",
            "📥 Import & 📤 Ekspor Tugas"
        ])

        # 1. LIST & HAPUS TUGAS
        with t_list:
            docs = db.collection("tugas_pancasila").stream()
            tugas_data = [{"id": d.id, **d.to_dict()} for d in docs]
            if tugas_data:
                for tg in tugas_data:
                    tipe_label = "🔘 Pilihan Ganda" if tg.get("tipe") == "pg" else "✏️ Essay"
                    target_k = tg.get("target_kelas", [])
                    target_str = ", ".join(target_k) if target_k else "Semua Kelas"

                    with st.expander(f"{tipe_label} - {tg.get('judul')} (🏫 Kelas: {target_str})"):
                        st.write(f"**Target Kelas:** `{target_str}`")
                        st.write(f"**Instruksi:** {tg.get('instruksi')}")
                        st.write("**Daftar Soal:**")
                        for idx, s in enumerate(tg.get("soal", []), 1):
                            if tg.get("tipe") == "pg":
                                st.markdown(f"**{idx}. {s.get('pertanyaan')}**")
                                for o_idx, opt in enumerate(s.get("opsi", [])):
                                    k_mark = "✅ (Kunci)" if o_idx == s.get("kunci") else ""
                                    st.write(f"   - {['A','B','C','D'][o_idx]}. {opt} {k_mark}")
                            else:
                                q_text = s.get("pertanyaan") if isinstance(s, dict) else str(s)
                                st.markdown(f"**{idx}. {q_text}**")
                        
                        if st.button(f"🗑️ Hapus Tugas Ini", key=f"del_tg_{tg['id']}"):
                            db.collection("tugas_pancasila").document(tg["id"]).delete()
                            st.success(f"✅ Berhasil! Tugas '{tg.get('judul')}' telah dihapus dari sistem.")
                            st.rerun()
            else:
                st.info("Belum ada tugas yang dibuat.")

        # 2. BUAT TUGAS BARU (MANUAL)
        with t_buat:
            judul_tugas = st.text_input("Judul Tugas / Kuis")
            instruksi = st.text_area("Instruksi / Petunjuk Pengerjaan")
            
            if pilihan_kelas_tugas:
                target_kelas_selected = st.multiselect(
                    "🏫 Ditujukan Untuk Kelas Mana Saja?",
                    options=pilihan_kelas_tugas,
                    default=pilihan_kelas_tugas,
                    help="Pilih satu atau lebih kelas yang wajib mengerjakan tugas ini."
                )
            else:
                target_kelas_selected = []
                st.warning("⚠️ Belum ada data kelas yang terdaftar!")

            tipe_tugas = st.radio("Tipe Soal Tugas", ["Pilihan Ganda", "Essay"])

            if tipe_tugas == "Pilihan Ganda":
                st.subheader("➕ Konfigurasi Soal Pilihan Ganda")
                num_soal = st.number_input("Jumlah Soal", min_value=1, max_value=20, value=2)
                
                soal_pg_list = []
                with st.form("form_buat_pg"):
                    for i in range(int(num_soal)):
                        st.markdown(f"--- \n**Soal No. {i+1}**")
                        q_txt = st.text_area(f"Pertanyaan No. {i+1}", key=f"q_pg_{i}")
                        c1, c2 = st.columns(2)
                        with c1:
                            op0 = st.text_input(f"Opsi A", key=f"op0_{i}")
                            op1 = st.text_input(f"Opsi B", key=f"op1_{i}")
                        with c2:
                            op2 = st.text_input(f"Opsi C", key=f"op2_{i}")
                            op3 = st.text_input(f"Opsi D", key=f"op3_{i}")
                        kunci = st.selectbox(
                            f"Kunci Jawaban Benar", 
                            [0, 1, 2, 3], 
                            format_func=lambda x: ["Opsi A", "Opsi B", "Opsi C", "Opsi D"][x], 
                            key=f"kunci_{i}"
                        )
                        soal_pg_list.append({"q": q_txt, "opsi": [op0, op1, op2, op3], "kunci": kunci})
                    
                    sub_pg = st.form_submit_button("Simpan Tugas Pilihan Ganda")
                    if sub_pg:
                        if not target_kelas_selected:
                            st.warning("Pilih minimal satu kelas target untuk tugas ini!")
                        elif judul_tugas and all(s["q"] and all(s["opsi"]) for s in soal_pg_list):
                            db.collection("tugas_pancasila").add({
                                "judul": judul_tugas,
                                "instruksi": instruksi,
                                "tipe": "pg",
                                "target_kelas": target_kelas_selected,
                                "soal": [{"pertanyaan": s["q"], "opsi": s["opsi"], "kunci": s["kunci"]} for s in soal_pg_list],
                                "created_by": user_info["username"],
                                "created_at": firestore.SERVER_TIMESTAMP
                            })
                            st.success(f"✅ Berhasil! Tugas PG '{judul_tugas}' berhasil dibuat dan disebarkan ke kelas: {', '.join(target_kelas_selected)}.")
                            st.rerun()
                        else:
                            st.warning("Mohon lengkapi judul, seluruh pertanyaan, dan pilihan opsi!")

            else:
                st.subheader("➕ Konfigurasi Soal Essay (Dinilai Otomatis oleh AI)")
                st.info("💡 **Kemudahan Guru**: AI Gemini akan menilai secara otomatis berdasarkan jawaban ideal.")
                
                num_essay = st.number_input("Jumlah Pertanyaan Essay", min_value=1, max_value=10, value=2)
                soal_essay_list = []
                with st.form("form_buat_essay"):
                    for i in range(int(num_essay)):
                        st.markdown(f"**Soal No. {i+1}**")
                        q_es = st.text_area(f"Pertanyaan Essay No. {i+1}", key=f"q_es_{i}")
                        soal_essay_list.append({"pertanyaan": q_es})
                    
                    sub_es = st.form_submit_button("Simpan Tugas Essay")
                    if sub_es:
                        if not target_kelas_selected:
                            st.warning("Pilih minimal satu kelas target untuk tugas ini!")
                        elif judul_tugas and all(q["pertanyaan"].strip() for q in soal_essay_list):
                            db.collection("tugas_pancasila").add({
                                "judul": judul_tugas,
                                "instruksi": instruksi,
                                "tipe": "essay",
                                "target_kelas": target_kelas_selected,
                                "soal": soal_essay_list,
                                "created_by": user_info["username"],
                                "created_at": firestore.SERVER_TIMESTAMP
                            })
                            st.success(f"✅ Berhasil! Tugas Essay '{judul_tugas}' berhasil dibuat dan disebarkan ke kelas: {', '.join(target_kelas_selected)}.")
                            st.rerun()
                        else:
                            st.warning("Mohon isi judul dan seluruh pertanyaan essay!")

        # 3. EDIT / UPDATE TUGAS
        with t_edit:
            st.subheader("✏️ Edit / Perbarui Tugas")
            docs_all_tugas = db.collection("tugas_pancasila").stream()
            tugas_edit_list = [{"id": d.id, **d.to_dict()} for d in docs_all_tugas]

            if tugas_edit_list:
                edit_map = {t["id"]: f"[{'PG' if t.get('tipe')=='pg' else 'ESSAY'}] {t.get('judul')}" for t in tugas_edit_list}
                selected_edit_id = st.selectbox("Pilih Tugas yang Ingin Diperbarui", list(edit_map.keys()), format_func=lambda x: edit_map[x])
                target_edit = next(t for t in tugas_edit_list if t["id"] == selected_edit_id)

                with st.form("form_edit_tugas_data"):
                    st.write(f"**Tipe Soal:** `{'Pilihan Ganda' if target_edit.get('tipe') == 'pg' else 'Essay'}`")
                    new_judul = st.text_input("Judul Tugas Baru", value=target_edit.get("judul", ""))
                    new_instruksi = st.text_area("Instruksi Tugas Baru", value=target_edit.get("instruksi", ""))
                    
                    curr_target_k = target_edit.get("target_kelas", [])
                    default_kelas_edit = [k for k in curr_target_k if k in pilihan_kelas_tugas] if isinstance(curr_target_k, list) else pilihan_kelas_tugas
                    new_target_k = st.multiselect("Target Kelas Baru", options=pilihan_kelas_tugas, default=default_kelas_edit)

                    btn_update_tugas = st.form_submit_button("Simpan Perubahan Tugas")

                    if btn_update_tugas:
                        if not new_judul.strip():
                            st.warning("Judul tugas tidak boleh kosong!")
                        elif not new_target_k:
                            st.warning("Pilih minimal satu kelas target!")
                        else:
                            db.collection("tugas_pancasila").document(selected_edit_id).update({
                                "judul": new_judul,
                                "instruksi": new_instruksi,
                                "target_kelas": new_target_k,
                                "updated_at": firestore.SERVER_TIMESTAMP
                            })
                            st.success(f"✅ Berhasil! Informasi tugas '{new_judul}' telah diperbarui.")
                            st.rerun()
            else:
                st.info("Belum ada tugas yang dapat diperbarui.")

        # 4. IMPORT & EKSPOR TUGAS
        with t_imp_exp:
            st.subheader("📥 Import & 📤 Ekspor Soal Tugas")
            col_imp_t, col_exp_t = st.columns([1, 1])

            with col_imp_t:
                st.markdown("### 📥 Import Soal dari File (CSV / Excel)")
                
                tipe_import = st.selectbox("Pilih Tipe Tugas yang Diimpor", ["Pilihan Ganda (PG)", "Essay"])
                judul_imp = st.text_input("Judul Tugas", placeholder="Contoh: Kuis Bab 2 Pancasila", key="imp_judul")
                instruksi_imp = st.text_area("Instruksi Tugas", placeholder="Contoh: Kerjakan dengan teliti!", key="imp_instruksi")

                target_kelas_imp = st.multiselect(
                    "🏫 Ditujukan Untuk Kelas Mana Saja?",
                    options=pilihan_kelas_tugas,
                    default=pilihan_kelas_tugas,
                    key="imp_target_kelas"
                )

                st.write("**📄 Unduh File Template Format:**")
                if tipe_import == "Pilihan Ganda (PG)":
                    df_tmpl_pg = pd.DataFrame([
                        {
                            "pertanyaan": "Apa lambang Sila ke-1 Pancasila?",
                            "opsi_a": "Bintang",
                            "opsi_b": "Rantai",
                            "opsi_c": "Pohon Beringin",
                            "opsi_d": "Kepala Banteng",
                            "kunci": "A"
                        }
                    ])
                    csv_pg_template = df_tmpl_pg.to_csv(index=False).encode('utf-8')
                    st.download_button(
                        label="📄 Unduh Template Soal PG (CSV)",
                        data=csv_pg_template,
                        file_name="template_soal_pg.csv",
                        mime="text/csv"
                    )
                else:
                    df_tmpl_essay = pd.DataFrame([
                        {"pertanyaan": "Jelaskan makna Sila ke-3 Pancasila dalam kehidupan sehari-hari!"}
                    ])
                    csv_es_template = df_tmpl_essay.to_csv(index=False).encode('utf-8')
                    st.download_button(
                        label="📄 Unduh Template Soal Essay (CSV)",
                        data=csv_es_template,
                        file_name="template_soal_essay.csv",
                        mime="text/csv"
                    )

                st.divider()

                file_soal = st.file_uploader("Unggah File Soal (.csv atau .xlsx)", type=["csv", "xlsx"], key="file_soal_upload")
                
                if file_soal is not None:
                    try:
                        df_soal = safe_read_uploaded_file(file_soal)
                        df_soal.columns = [str(col).strip().lower() for col in df_soal.columns]

                        if tipe_import == "Pilihan Ganda (PG)":
                            req_cols = ["pertanyaan", "opsi_a", "opsi_b", "opsi_c", "opsi_d", "kunci"]
                            if not all(c in df_soal.columns for c in req_cols):
                                st.error(f"Format file tidak sesuai! Wajib memiliki kolom: **{', '.join(req_cols)}**")
                            else:
                                st.write("👀 **Pratinjau Soal Pilihan Ganda:**")
                                st.dataframe(df_soal, use_container_width=True)

                                if st.button("🚀 Import & Terbitkan Tugas PG", type="primary"):
                                    if not judul_imp:
                                        st.warning("Mohon isi 'Judul Tugas' terlebih dahulu!")
                                    elif not target_kelas_imp:
                                        st.warning("Pilih minimal satu kelas target!")
                                    else:
                                        parsed_soal = []
                                        key_mapping = {'a': 0, 'b': 1, 'c': 2, 'd': 3, '0': 0, '1': 1, '2': 2, '3': 3}
                                        
                                        for _, row in df_soal.iterrows():
                                            k_raw = str(row["kunci"]).strip().lower()
                                            k_val = key_mapping.get(k_raw, 0)

                                            parsed_soal.append({
                                                "pertanyaan": str(row["pertanyaan"]).strip(),
                                                "opsi": [
                                                    str(row["opsi_a"]).strip(),
                                                    str(row["opsi_b"]).strip(),
                                                    str(row["opsi_c"]).strip(),
                                                    str(row["opsi_d"]).strip()
                                                ],
                                                "kunci": k_val
                                            })

                                        db.collection("tugas_pancasila").add({
                                            "judul": judul_imp,
                                            "instruksi": instruksi_imp,
                                            "tipe": "pg",
                                            "target_kelas": target_kelas_imp,
                                            "soal": parsed_soal,
                                            "created_by": user_info["username"],
                                            "created_at": firestore.SERVER_TIMESTAMP
                                        })
                                        st.success(f"✅ Berhasil! Tugas PG '{judul_imp}' ({len(parsed_soal)} soal) berhasil diimpor ke kelas: {', '.join(target_kelas_imp)}.")
                                        st.rerun()

                        else:
                            req_cols = ["pertanyaan"]
                            if not all(c in df_soal.columns for c in req_cols):
                                st.error("Format file tidak sesuai! Wajib memiliki kolom: **pertanyaan**")
                            else:
                                st.write("👀 **Pratinjau Soal Essay:**")
                                st.dataframe(df_soal, use_container_width=True)

                                if st.button("🚀 Import & Terbitkan Tugas Essay", type="primary"):
                                    if not judul_imp:
                                        st.warning("Mohon isi 'Judul Tugas' terlebih dahulu!")
                                    elif not target_kelas_imp:
                                        st.warning("Pilih minimal satu kelas target!")
                                    else:
                                        parsed_soal = [{"pertanyaan": str(row["pertanyaan"]).strip()} for _, row in df_soal.iterrows() if str(row["pertanyaan"]).strip()]

                                        db.collection("tugas_pancasila").add({
                                            "judul": judul_imp,
                                            "instruksi": instruksi_imp,
                                            "tipe": "essay",
                                            "target_kelas": target_kelas_imp,
                                            "soal": parsed_soal,
                                            "created_by": user_info["username"],
                                            "created_at": firestore.SERVER_TIMESTAMP
                                        })
                                        st.success(f"✅ Berhasil! Tugas Essay '{judul_imp}' ({len(parsed_soal)} soal) berhasil diimpor ke kelas: {', '.join(target_kelas_imp)}.")
                                        st.rerun()

                    except Exception as e:
                        st.error(f"Gagal membaca file: {e}")

            with col_exp_t:
                st.markdown("### 📤 Ekspor / Backup Soal Tugas")
                docs_tg = db.collection("tugas_pancasila").stream()
                all_tg = [{"id": d.id, **d.to_dict()} for d in docs_tg]

                if all_tg:
                    tg_map = {t["id"]: f"[{'PG' if t.get('tipe')=='pg' else 'ESSAY'}] {t.get('judul')}" for t in all_tg}
                    sel_exp_id = st.selectbox("Pilih Tugas yang Ingin Diekspor", list(tg_map.keys()), format_func=lambda x: tg_map[x])
                    
                    target_tg = next(t for t in all_tg if t["id"] == sel_exp_id)
                    exp_records = []
                    if target_tg.get("tipe") == "pg":
                        for idx, s in enumerate(target_tg.get("soal", []), 1):
                            opsi_list = s.get("opsi", ["", "", "", ""])
                            kunci_num = s.get("kunci", 0)
                            kunci_letter = ["A", "B", "C", "D"][kunci_num] if 0 <= kunci_num <= 3 else "A"
                            exp_records.append({
                                "no": idx,
                                "pertanyaan": s.get("pertanyaan", ""),
                                "opsi_a": opsi_list[0] if len(opsi_list) > 0 else "",
                                "opsi_b": opsi_list[1] if len(opsi_list) > 1 else "",
                                "opsi_c": opsi_list[2] if len(opsi_list) > 2 else "",
                                "opsi_d": opsi_list[3] if len(opsi_list) > 3 else "",
                                "kunci": kunci_letter
                            })
                    else:
                        for idx, s in enumerate(target_tg.get("soal", []), 1):
                            q_text = s.get("pertanyaan") if isinstance(s, dict) else str(s)
                            exp_records.append({"no": idx, "pertanyaan": q_text})

                    if exp_records:
                        df_exp_tg = pd.DataFrame(exp_records)
                        st.dataframe(df_exp_tg, use_container_width=True, hide_index=True)
                        file_name_clean = re.sub(r'[^a-zA-Z0-9]', '_', target_tg.get("judul", "tugas"))
                        
                        c_down1, c_down2 = st.columns(2)
                        with c_down1:
                            csv_exp_data = df_exp_tg.to_csv(index=False).encode('utf-8')
                            st.download_button(
                                label="💾 Unduh Soal (CSV)",
                                data=csv_exp_data,
                                file_name=f"soal_{file_name_clean}.csv",
                                mime="text/csv"
                            )
                        with c_down2:
                            try:
                                buffer_tg = io.BytesIO()
                                with pd.ExcelWriter(buffer_tg, engine='openpyxl') as writer:
                                    df_exp_tg.to_excel(writer, index=False, sheet_name='Soal')
                                st.download_button(
                                    label="📊 Unduh Soal (Excel .xlsx)",
                                    data=buffer_tg.getvalue(),
                                    file_name=f"soal_{file_name_clean}.xlsx",
                                    mime="application/vnd.openxmlformats-officedocument.spreadsheetml.sheet"
                                )
                            except ModuleNotFoundError:
                                st.info("Install `openpyxl` untuk unduh format .xlsx")
                else:
                    st.info("Belum ada tugas terdaftar yang dapat diekspor.")

    # --- 7C. REKAP & PERIKSA NILAI SISWA (DENGAN AI) ---
    elif menu == "📊 Rekap & Periksa Nilai":
        st.header("📊 Rekap & Periksa Nilai Siswa")

        tugas_docs = db.collection("tugas_pancasila").stream()
        list_tugas = [{"id": d.id, **d.to_dict()} for d in tugas_docs]

        siswa_docs = db.collection("users").where("role", "==", "siswa").stream()
        list_siswa = []
        k_ajar = user_info.get("kelas_ajar", [])
        
        for d in siswa_docs:
            u = d.to_dict()
            if not k_ajar or u.get("kelas") in k_ajar:
                list_siswa.append({
                    "username": d.id,
                    "nama": u.get("nama", ""),
                    "kelas": u.get("kelas", "-")
                })

        jwb_docs = db.collection("jawaban_siswa").stream()
        submissions = {}
        for d in jwb_docs:
            j = d.to_dict()
            submissions[(j.get("username_siswa"), j.get("id_tugas"))] = {"id": d.id, **j}

        records = []
        for s in list_siswa:
            for t in list_tugas:
                if is_tugas_sesuai_kelas(t, s["kelas"]):
                    key = (s["username"], t["id"])
                    sub = submissions.get(key)
                    
                    if sub:
                        n_val = sub.get("nilai")
                        if n_val is not None:
                            status = "Sudah Dinilai"
                            score_text = str(n_val)
                        else:
                            status = "Belum Dinilai (Perlu Diperiksa)"
                            score_text = "⏳ Belum Dinilai"
                    else:
                        status = "Belum Dikerjakan"
                        score_text = "-"
                        n_val = -1

                    records.append({
                        "Nama Siswa": s["nama"],
                        "Username": s["username"],
                        "Kelas": s["kelas"],
                        "Nama Tugas": t.get("judul", "-"),
                        "Tipe": "PG" if t.get("tipe") == "pg" else "Essay",
                        "Status": status,
                        "Nilai": score_text,
                        "Catatan Guru": sub.get("catatan_guru", "") if sub else "",
                        "raw_nilai": n_val if n_val is not None else -1,
                        "sub_doc": sub,
                        "id_tugas": t["id"]
                    })

        if records:
            df_all = pd.DataFrame(records)

            col_f1, col_f2, col_f3, col_f4 = st.columns([2, 1.2, 1.2, 1.4])
            with col_f1:
                search_query = st.text_input("🔍 Cari (Nama Siswa / Nama Tugas)", placeholder="Ketik nama...").strip().lower()
            with col_f2:
                opt_kelas = ["Semua Kelas"] + sorted([k for k in df_all["Kelas"].unique() if k])
                sel_kelas = st.selectbox("🏫 Filter Kelas", opt_kelas)
            with col_f3:
                opt_tugas = ["Semua Tugas"] + sorted([t for t in df_all["Nama Tugas"].unique() if t])
                sel_tugas = st.selectbox("📋 Filter Tugas", opt_tugas)
            with col_f4:
                opt_status = ["Semua Status", "Sudah Dinilai", "Belum Dinilai (Perlu Diperiksa)", "Belum Dikerjakan"]
                sel_status = st.selectbox("📌 Filter Status", opt_status)

            df_filtered = df_all.copy()
            if search_query:
                df_filtered = df_filtered[
                    df_filtered["Nama Siswa"].str.lower().str.contains(search_query) |
                    df_filtered["Nama Tugas"].str.lower().str.contains(search_query)
                ]
            if sel_kelas != "Semua Kelas":
                df_filtered = df_filtered[df_filtered["Kelas"] == sel_kelas]
            if sel_tugas != "Semua Tugas":
                df_filtered = df_filtered[df_filtered["Nama Tugas"] == sel_tugas]
            if sel_status != "Semua Status":
                df_filtered = df_filtered[df_filtered["Status"] == sel_status]

            df_display = df_filtered[["Nama Siswa", "Kelas", "Nama Tugas", "Tipe", "Status", "Nilai", "Catatan Guru"]]
            st.dataframe(df_display, use_container_width=True, hide_index=True)

            perlu_diperiksa = df_filtered[df_filtered["Status"] == "Belum Dinilai (Perlu Diperiksa)"]
            if not perlu_diperiksa.empty:
                st.divider()
                st.subheader("✏️ Form Penilaian Jawaban Essay (Perlu Diperiksa)")

                for i, (idx, row) in enumerate(perlu_diperiksa.iterrows()):
                    sub = row["sub_doc"]
                    doc_id = str(sub.get('id', ''))
                    
                    siswa_clean = "".join(filter(str.isalnum, str(row.get('Nama Siswa', ''))))
                    tugas_clean = "".join(filter(str.isalnum, str(row.get('Nama Tugas', ''))))
                    unique_key = f"{doc_id}_{siswa_clean}_{tugas_clean}_{idx}_{i}"

                    with st.expander(f"👤 {row['Nama Siswa']} ({row['Kelas']}) — {row['Nama Tugas']}"):
                        soal_list = sub.get("soal", [])
                        jwb_list = sub.get("jawaban", [])

                        for s_idx, (soal, jawab) in enumerate(zip(soal_list, jwb_list), 1):
                            q_text = soal.get('pertanyaan') if isinstance(soal, dict) else str(soal)
                            st.markdown(f"**{s_idx}. {q_text}**")
                            st.info(jawab if jawab else "*Siswa tidak mengisi jawaban*")

                        if f"n_in_{unique_key}" not in st.session_state:
                            st.session_state[f"n_in_{unique_key}"] = 80
                        if f"c_in_{unique_key}" not in st.session_state:
                            st.session_state[f"c_in_{unique_key}"] = sub.get("catatan_guru", "")

                        col_ai, _ = st.columns([1.5, 2.5])
                        with col_ai:
                            if st.button(f"🤖 Koreksi Otomatis dengan AI", key=f"btn_ai_{unique_key}"):
                                with st.spinner("🤖 Gemini AI sedang menganalisis..."):
                                    val_ai, cat_ai = koreksi_essay_dengan_ai(soal_list, jwb_list)
                                    if val_ai is not None:
                                        st.session_state[f"n_in_{unique_key}"] = int(val_ai)
                                        st.session_state[f"c_in_{unique_key}"] = str(cat_ai)
                                        st.success("✅ Berhasil! Koreksi otomatis AI selesai.")
                                        st.rerun()
                                    else:
                                        st.error(cat_ai)

                        with st.form(key=f"form_essay_eval_{unique_key}"):
                            n_in = st.number_input("Input Nilai Akhir (0 - 100)", min_value=0, max_value=100, key=f"n_in_{unique_key}")
                            c_in = st.text_area("Catatan / Feedback Guru", key=f"c_in_{unique_key}")
                            btn_save = st.form_submit_button("💾 Simpan Nilai Ke Sistem")

                            if btn_save:
                                db.collection("jawaban_siswa").document(doc_id).update({
                                    "nilai": n_in,
                                    "catatan_guru": c_in,
                                    "dinilai_pada": firestore.SERVER_TIMESTAMP
                                })
                                st.success("✅ Berhasil! Nilai telah disimpan ke sistem.")
                                st.rerun()
        else:
            st.info("Belum ada data nilai atau tugas yang tersedia.")

# ==========================================
# 8. PANEL SISWA
# ==========================================
elif role == "siswa":
    st.title("🇮🇩 Ruang Siswa")
    menu_s = st.sidebar.radio(
        "📌 Menu Siswa",
        ["📚 Modul Materi", "✍️ Kerjakan Tugas", "📊 Riwayat & Nilai Saya"]
    )
    kelas_siswa = user_info.get("kelas", "")

    # --- 8A. MODUL MATERI ---
    if menu_s == "📚 Modul Materi":
        st.header("📚 Modul Materi Pembelajaran")
        docs = db.collection("materi_pancasila").stream()
        materi_list = [{"id": d.id, **d.to_dict()} for d in docs]
        if materi_list:
            for item in materi_list:
                with st.expander(f"📘 {item.get('bab')}: {item.get('judul')}"):
                    st.markdown(item.get("konten"))
        else:
            st.info("Materi pembelajaran belum tersedia.")

    # --- 8B. KERJAKAN TUGAS ---
    elif menu_s == "✍️ Kerjakan Tugas":
        st.header("✍️ Lembar Pengerjaan Tugas & Kuis")
        docs = db.collection("tugas_pancasila").stream()
        raw_tugas = [{"id": d.id, **d.to_dict()} for d in docs]
        tugas_list = [t for t in raw_tugas if is_tugas_sesuai_kelas(t, kelas_siswa)]

        if tugas_list:
            tugas_map = {t["id"]: f"[{'PG' if t.get('tipe')=='pg' else 'ESSAY'}] {t.get('judul')}" for t in tugas_list}
            selected_tugas_id = st.selectbox("Pilih Tugas yang Ingin Dikerjakan", list(tugas_map.keys()), format_func=lambda x: tugas_map[x])
            tugas_active = next(t for t in tugas_list if t["id"] == selected_tugas_id)
            
            cek_jwb = db.collection("jawaban_siswa")\
                .where("id_tugas", "==", selected_tugas_id)\
                .where("username_siswa", "==", user_info["username"]).get()

            if len(cek_jwb) > 0:
                st.warning("⚠️ Anda sudah pernah mengumpulkan tugas ini!")
            else:
                st.subheader(f"📋 {tugas_active.get('judul')}")
                st.write(f"**Instruksi:** {tugas_active.get('instruksi')}")
                st.divider()

                if tugas_active.get("tipe") == "pg":
                    with st.form("form_kerjakan_pg"):
                        jawaban_pg = []
                        for i, s in enumerate(tugas_active.get("soal", []), 1):
                            st.markdown(f"**{i}. {s.get('pertanyaan')}**")
                            ans = st.radio(
                                "Pilih Jawaban:",
                                options=[0, 1, 2, 3],
                                format_func=lambda x, opts=s.get("opsi"): f"{['A','B','C','D'][x]}. {opts[x]}",
                                key=f"ans_pg_{tugas_active['id']}_{i}"
                            )
                            jawaban_pg.append(ans)
                        
                        btn_sub_pg = st.form_submit_button("Kumpulkan Jawaban PG")

                        if btn_sub_pg:
                            benar = sum(1 for idx, s in enumerate(tugas_active.get("soal", [])) if jawaban_pg[idx] == s.get("kunci"))
                            total_soal = len(tugas_active.get("soal", []))
                            nilai_pg = round((benar / total_soal) * 100)
                            
                            db.collection("jawaban_siswa").add({
                                "id_tugas": selected_tugas_id,
                                "judul_tugas": tugas_active.get("judul"),
                                "username_siswa": user_info["username"],
                                "nama_siswa": user_info["nama"],
                                "kelas_siswa": kelas_siswa,
                                "tipe": "pg",
                                "jawaban": jawaban_pg,
                                "nilai": nilai_pg,
                                "catatan_guru": "Diperiksa otomatis oleh sistem",
                                "submitted_at": firestore.SERVER_TIMESTAMP
                            })
                            st.success(f"✅ Berhasil dikumpulkan! Nilai Anda: {nilai_pg}/100.")
                            st.rerun()

                else:
                    with st.form("form_kerjakan_essay"):
                        jawaban_essay = []
                        soals = tugas_active.get("soal", [])
                        for i, q in enumerate(soals, 1):
                            q_text = q.get("pertanyaan") if isinstance(q, dict) else q
                            st.markdown(f"**{i}. {q_text}**")
                            ans_es = st.text_area("Jawaban Anda:", key=f"ans_es_{tugas_active['id']}_{i}", height=120)
                            jawaban_essay.append(ans_es)
                        
                        btn_sub_es = st.form_submit_button("Kumpulkan Jawaban Essay")

                        if btn_sub_es:
                            if all(a.strip() for a in jawaban_essay):
                                db.collection("jawaban_siswa").add({
                                    "id_tugas": selected_tugas_id,
                                    "judul_tugas": tugas_active.get("judul"),
                                    "username_siswa": user_info["username"],
                                    "nama_siswa": user_info["nama"],
                                    "kelas_siswa": kelas_siswa,
                                    "tipe": "essay",
                                    "soal": soals,
                                    "jawaban": jawaban_essay,
                                    "nilai": None,
                                    "catatan_guru": "",
                                    "submitted_at": firestore.SERVER_TIMESTAMP
                                })
                                st.success("✅ Berhasil! Jawaban Essay telah dikumpulkan. Menunggu koreksi guru.")
                                st.rerun()
                            else:
                                st.warning("Mohon isi seluruh pertanyaan essay sebelum mengumpulkan!")
        else:
            st.info("Belum ada tugas yang dipublikasikan untuk kelas Anda.")

    # --- 8C. RIWAYAT & NILAI SAYA ---
    elif menu_s == "📊 Riwayat & Nilai Saya":
        st.header("📊 Riwayat Tugas & Hasil Nilai Saya")

        tugas_docs = db.collection("tugas_pancasila").stream()
        raw_tugas = [{"id": d.id, **d.to_dict()} for d in tugas_docs]
        list_tugas = [t for t in raw_tugas if is_tugas_sesuai_kelas(t, kelas_siswa)]

        docs = db.collection("jawaban_siswa").where("username_siswa", "==", user_info["username"]).stream()
        sub_dict = {d.to_dict().get("id_tugas"): {"id": d.id, **d.to_dict()} for d in docs}

        if list_tugas:
            s_records = []
            for t in list_tugas:
                sub = sub_dict.get(t["id"])
                if sub:
                    n_val = sub.get("nilai")
                    status = "✅ Sudah Dinilai" if n_val is not None else "⏳ Menunggu Penilaian Guru"
                    score_disp = f"{n_val} / 100" if n_val is not None else "-"
                    catatan = sub.get("catatan_guru", "-")
                else:
                    status = "❌ Belum Dikerjakan"
                    score_disp = "-"
                    catatan = "-"

                s_records.append({
                    "Nama Tugas": t.get("judul", "-"),
                    "Tipe": "Pilihan Ganda" if t.get("tipe") == "pg" else "Essay",
                    "Status": status,
                    "Nilai": score_disp,
                    "Catatan Guru": catatan if catatan else "-"
                })

            df_siswa = pd.DataFrame(s_records)
            st.dataframe(df_siswa, use_container_width=True, hide_index=True)
        else:
            st.info("Belum ada tugas yang dipublikasikan untuk kelas Anda.")
