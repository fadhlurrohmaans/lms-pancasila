import streamlit as st
import firebase_admin
from firebase_admin import credentials, firestore
import pandas as pd
import hashlib
import io

# ==========================================
# 1. CONFIG & CSS MOBILE RESPONSIVE
# ==========================================
st.set_page_config(
    page_title="LMS Pendidikan Pancasila",
    page_icon="🇮🇩",
    layout="wide",
    initial_sidebar_state="auto"
)

# Injeksi Custom CSS untuk Tampilan Responsive (HP Android & iOS)
st.markdown("""
    <style>
    /* 1. Mencegah Auto-Zoom di iOS Safari & Menyesuaikan Input Mobile */
    input[type="text"], input[type="password"], textarea, select {
        font-size: 16px !important;
    }
    
    /* 2. Optimasi Padding Container di Layar Kecil / HP */
    @media (max-width: 768px) {
        .main .block-container {
            padding-left: 0.8rem !important;
            padding-right: 0.8rem !important;
            padding-top: 1rem !important;
            padding-bottom: 2rem !important;
        }
        
        /* 3. Auto-Stacking Kolom di Mobile */
        [data-testid="column"] {
            width: 100% !important;
            flex: 1 1 100% !important;
            min-width: 100% !important;
            margin-bottom: 0.5rem;
        }

        /* 4. Tombol Touch-Friendly */
        .stButton > button, .stDownloadButton > button {
            width: 100% !important;
            min-height: 48px !important;
            font-size: 16px !important;
            font-weight: bold;
            border-radius: 8px !important;
            margin-top: 4px;
            margin-bottom: 4px;
        }
        
        /* 5. Judul & Subjudul Responsif */
        h1 { font-size: 1.8rem !important; }
        h2 { font-size: 1.4rem !important; }
        h3 { font-size: 1.2rem !important; }
    }

    /* 6. Membuat Tab Dapat Di-scroll Menyamping di HP */
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
# 2. INISIALISASI FIREBASE FIRESTORE
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

# Helper Hashing Password
def hash_pass(password):
    return hashlib.sha256(password.encode()).hexdigest()

# Helper untuk mengambil seluruh daftar master kelas
def get_all_kelas():
    docs = db.collection("kelas").stream()
    return sorted([d.id for d in docs])

# ==========================================
# 3. SEEDING DEFAULT SUPER ADMIN
# ==========================================
def init_super_admin():
    """Membuat akun default Super Admin jika database masih kosong/belum ada admin"""
    admin_ref = db.collection("users").document("admin").get()
    if not admin_ref.exists:
        db.collection("users").document("admin").set({
            "nama": "Super Admin",
            "password": hash_pass("admin123"),
            "role": "superadmin",
            "created_at": firestore.SERVER_TIMESTAMP
        })

init_super_admin()

# Inisialisasi Session State
if "user" not in st.session_state:
    st.session_state["user"] = None

# ==========================================
# 4. HALAMAN LOGIN
# ==========================================
if st.session_state["user"] is None:
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
                            st.success(f"Selamat datang, {user_data.get('nama')}!")
                            st.rerun()
                        else:
                            st.error("Password salah!")
                    else:
                        st.error("Username tidak terdaftar! Hubungi Super Admin.")
                else:
                    st.warning("Mohon isi username dan password.")

    st.stop()  # Hentikan eksekusi jika belum login

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
# 6. PANEL SUPER ADMIN (KELOLA AKUN & MASTER KELAS)
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
                        st.success(f"Kelas '{new_kelas_name}' berhasil ditambahkan!")
                        st.rerun()
                    else:
                        st.warning("Nama kelas tidak boleh kosong!")

            if daftar_kelas_aktif:
                st.divider()
                st.write("🗑️ **Hapus Kelas**")
                del_k_name = st.selectbox("Pilih Kelas yang Ingin Dihapus", daftar_kelas_aktif)
                if st.button("Hapus Kelas Ini", type="primary"):
                    db.collection("kelas").document(del_k_name).delete()
                    st.success(f"Kelas '{del_k_name}' berhasil dihapus!")
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

    # --- 6C. ADD USER ---
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
                                "role": new_role.lower(),
                                "created_at": firestore.SERVER_TIMESTAMP
                            }
                            
                            if new_role == "Siswa":
                                data_user["kelas"] = kelas_siswa_selected
                            elif new_role == "Guru":
                                data_user["kelas_ajar"] = kelas_guru_selected

                            db.collection("users").document(new_username).set(data_user)
                            st.success(f"Akun {new_role} '{new_username}' berhasil dibuat!")
                            st.rerun()
                else:
                    st.warning("Mohon lengkapi seluruh isian formulir!")

    # --- 6D. IMPORT & EKSPOR DATA SISWA ---
    with tab_import_export:
        st.subheader("📥 Import & 📤 Ekspor Data Siswa")
        col_imp, col_exp = st.columns([1, 1])

        # Sub-Bagian Import Data Siswa
        with col_imp:
            st.markdown("### 📥 Import Data Siswa Massal")
            st.write("Gunakan file CSV atau Excel untuk memasukkan daftar siswa secara cepat.")
            
            # 1. Download Template CSV
            df_template = pd.DataFrame([
                {"nama": "Budi Santoso", "username": "budi123", "password": "siswa123", "kelas": "X IPA 1"},
                {"nama": "Siti Aminah", "username": "siti123", "password": "siswa123", "kelas": "X IPA 2"}
            ])
            csv_template = df_template.to_csv(index=False).encode('utf-8')
            
            st.download_button(
                label="📄 Download Template File (CSV)",
                data=csv_template,
                file_name="template_import_siswa.csv",
                mime="text/csv"
            )

            st.divider()

            # 2. Upload File Import
            uploaded_file = st.file_uploader("Unggah File Siswa (.csv atau .xlsx)", type=["csv", "xlsx"])
            if uploaded_file is not None:
                try:
                    if uploaded_file.name.endswith('.csv'):
                        df_import = pd.read_csv(uploaded_file)
                    else:
                        df_import = pd.read_excel(uploaded_file)

                    # Rapikan nama kolom
                    df_import.columns = [str(col).strip().lower() for col in df_import.columns]
                    req_cols = ["nama", "username", "password", "kelas"]

                    if not all(col in df_import.columns for col in req_cols):
                        st.error(f"Format file tidak valid! Wajib memiliki kolom: **{', '.join(req_cols)}**")
                    else:
                        st.write("👀 **Pratinjau Data:**")
                        st.dataframe(df_import, use_container_width=True)

                        if st.button("🚀 Mulai Import Data Siswa", type="primary"):
                            success_count = 0
                            skipped_count = 0
                            errors = []

                            for idx, row in df_import.iterrows():
                                u_name = str(row["username"]).strip().lower()
                                p_word = str(row["password"]).strip()
                                nama_s = str(row["nama"]).strip()
                                kelas_s = str(row["kelas"]).strip()

                                if not u_name or not p_word or not nama_s:
                                    skipped_count += 1
                                    continue

                                # Cek username apakah sudah terdaftar
                                u_check = db.collection("users").document(u_name).get()
                                if u_check.exists:
                                    skipped_count += 1
                                    errors.append(f"Baris {idx+1}: Username '{u_name}' sudah dipakai.")
                                    continue

                                # Simpan data siswa ke Firestore
                                db.collection("users").document(u_name).set({
                                    "nama": nama_s,
                                    "password": hash_pass(p_word),
                                    "role": "siswa",
                                    "kelas": kelas_s,
                                    "created_at": firestore.SERVER_TIMESTAMP
                                })
                                success_count += 1

                            st.success(f"✅ Selesai! **{success_count}** data siswa berhasil diimpor.")
                            if skipped_count > 0:
                                st.warning(f"⚠️ **{skipped_count}** data dilewati (username bentrok/kolom kosong).")
                            if errors:
                                with st.expander("Lihat Detail Alasan Dilewati"):
                                    for err in errors:
                                        st.write(f"- {err}")
                            st.rerun()

                except Exception as e:
                    st.error(f"Gagal memproses file: {e}")

        # Sub-Bagian Ekspor Data Siswa
        with col_exp:
            st.markdown("### 📤 Ekspor Data Siswa")
            st.write("Unduh daftar siswa yang telah terdaftar di database.")

            docs_siswa = db.collection("users").where("role", "==", "siswa").stream()
            data_siswa = []
            for d in docs_siswa:
                u_dict = d.to_dict()
                data_siswa.append({
                    "Username": d.id,
                    "Nama Lengkap": u_dict.get("nama", ""),
                    "Kelas": u_dict.get("kelas", "")
                })

            if data_siswa:
                df_export = pd.DataFrame(data_siswa)
                st.write(f"Total Siswa Terdaftar: **{len(data_siswa)}**")
                st.dataframe(df_export, use_container_width=True)

                # Ekspor ke CSV
                csv_data = df_export.to_csv(index=False).encode('utf-8')
                st.download_button(
                    label="💾 Unduh Data Siswa (CSV)",
                    data=csv_data,
                    file_name="daftar_siswa_lms.csv",
                    mime="text/csv"
                )

                # Ekspor ke Excel (.xlsx)
                buffer = io.BytesIO()
                with pd.ExcelWriter(buffer, engine='openpyxl') as writer:
                    df_export.to_excel(writer, index=False, sheet_name='Data Siswa')
                
                st.download_button(
                    label="📊 Unduh Data Siswa (Excel .xlsx)",
                    data=buffer.getvalue(),
                    file_name="daftar_siswa_lms.xlsx",
                    mime="application/vnd.openxmlformats-officedocument.spreadsheetml.sheet"
                )
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
                        st.success(f"Kelas untuk siswa {u_doc.get('nama')} berhasil diperbarui!")
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
                        st.success(f"Daftar kelas ajar untuk Guru {u_doc.get('nama')} berhasil diperbarui!")
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
                st.success("Akun berhasil dihapus!")
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
        ["📖 Kelola Materi", "📝 Buat & Kelola Tugas", "💯 Periksa Jawaban Essay"]
    )

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
                    st.success("Materi berhasil disimpan!")
                    st.rerun()

        with t3:
            docs = db.collection("materi_pancasila").stream()
            materi_list = [{"id": d.id, **d.to_dict()} for d in docs]
            if materi_list:
                m_map = {m["id"]: f"[{m.get('bab')}] {m.get('judul')}" for m in materi_list}
                sel_m = st.selectbox("Pilih Materi untuk Dihapus", list(m_map.keys()), format_func=lambda x: m_map[x])
                if st.button("Hapus Materi", type="primary"):
                    db.collection("materi_pancasila").document(sel_m).delete()
                    st.success("Materi berhasil dihapus!")
                    st.rerun()

    # --- 7B. BUAT & KELOLA TUGAS ---
    elif menu == "📝 Buat & Kelola Tugas":
        st.header("📝 Buat & Kelola Tugas")
        t_list, t_buat = st.tabs(["📋 Daftar Tugas", "➕ Buat Tugas Baru"])

        with t_list:
            docs = db.collection("tugas_pancasila").stream()
            tugas_data = [{"id": d.id, **d.to_dict()} for d in docs]
            if tugas_data:
                for tg in tugas_data:
                    tipe_label = "🔘 Pilihan Ganda" if tg.get("tipe") == "pg" else "✏️ Essay"
                    with st.expander(f"{tipe_label} - {tg.get('judul')}"):
                        st.write(f"**Instruksi:** {tg.get('instruksi')}")
                        st.write("**Daftar Soal:**")
                        for idx, s in enumerate(tg.get("soal", []), 1):
                            if tg.get("tipe") == "pg":
                                st.markdown(f"**{idx}. {s.get('pertanyaan')}**")
                                for o_idx, opt in enumerate(s.get("opsi", [])):
                                    k_mark = "✅ (Kunci)" if o_idx == s.get("kunci") else ""
                                    st.write(f"   - {['A','B','C','D'][o_idx]}. {opt} {k_mark}")
                            else:
                                st.markdown(f"**{idx}. {s}**")
                        
                        if st.button(f"🗑️ Hapus Tugas Ini", key=f"del_tg_{tg['id']}"):
                            db.collection("tugas_pancasila").document(tg["id"]).delete()
                            st.success("Tugas berhasil dihapus!")
                            st.rerun()
            else:
                st.info("Belum ada tugas yang dibuat.")

        with t_buat:
            judul_tugas = st.text_input("Judul Tugas / Kuis")
            instruksi = st.text_area("Instruksi / Petunjuk Pengerjaan")
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
                        if judul_tugas and all(s["q"] and all(s["opsi"]) for s in soal_pg_list):
                            db.collection("tugas_pancasila").add({
                                "judul": judul_tugas,
                                "instruksi": instruksi,
                                "tipe": "pg",
                                "soal": [{"pertanyaan": s["q"], "opsi": s["opsi"], "kunci": s["kunci"]} for s in soal_pg_list],
                                "created_by": user_info["username"],
                                "created_at": firestore.SERVER_TIMESTAMP
                            })
                            st.success("Tugas Pilihan Ganda berhasil diterbitkan!")
                            st.rerun()
                        else:
                            st.warning("Mohon lengkapi judul, seluruh pertanyaan, dan pilihan opsi!")

            else:  # Essay
                st.subheader("➕ Konfigurasi Soal Essay")
                num_essay = st.number_input("Jumlah Pertanyaan Essay", min_value=1, max_value=10, value=2)
                soal_essay_list = []
                with st.form("form_buat_essay"):
                    for i in range(int(num_essay)):
                        q_es = st.text_area(f"Pertanyaan Essay No. {i+1}", key=f"q_es_{i}")
                        soal_essay_list.append(q_es)
                    
                    sub_es = st.form_submit_button("Simpan Tugas Essay")
                    if sub_es:
                        if judul_tugas and all(q.strip() for q in soal_essay_list):
                            db.collection("tugas_pancasila").add({
                                "judul": judul_tugas,
                                "instruksi": instruksi,
                                "tipe": "essay",
                                "soal": soal_essay_list,
                                "created_by": user_info["username"],
                                "created_at": firestore.SERVER_TIMESTAMP
                            })
                            st.success("Tugas Essay berhasil diterbitkan!")
                            st.rerun()
                        else:
                            st.warning("Mohon isi judul dan semua pertanyaan essay!")

    # --- 7C. PERIKSA JAWABAN ESSAY ---
    elif menu == "💯 Periksa Jawaban Essay":
        st.header("💯 Periksa & Nilai Jawaban Essay Siswa")
        docs = db.collection("jawaban_siswa").where("tipe", "==", "essay").stream()
        jawaban_list = [{"id": d.id, **d.to_dict()} for d in docs]

        if jawaban_list:
            for jw in jawaban_list:
                status_nilai = f"✅ Nilai: {jw.get('nilai')}" if jw.get("nilai") is not None else "⏳ Belum Dinilai"
                with st.expander(f"👤 {jw.get('nama_siswa')} | Tugas: {jw.get('judul_tugas')} ({status_nilai})"):
                    st.write("**Jawaban Siswa:**")
                    for idx, (soal, jawab) in enumerate(zip(jw.get("soal", []), jw.get("jawaban", [])), 1):
                        st.markdown(f"**{idx}. {soal}**")
                        st.info(jawab if jawab else "*Tidak diisi*")

                    with st.form(f"form_nilai_{jw['id']}"):
                        val_now = int(jw.get("nilai")) if jw.get("nilai") is not None else 80
                        nilai_input = st.number_input("Input Nilai (0 - 100)", min_value=0, max_value=100, value=val_now)
                        catatan_input = st.text_area("Catatan / Feedback Guru", value=jw.get("catatan_guru", ""))
                        btn_simpan_nilai = st.form_submit_button("Simpan Nilai & Feedback")

                        if btn_simpan_nilai:
                            db.collection("jawaban_siswa").document(jw["id"]).update({
                                "nilai": nilai_input,
                                "catatan_guru": catatan_input,
                                "dinilai_pada": firestore.SERVER_TIMESTAMP
                            })
                            st.success("Nilai berhasil diperbarui!")
                            st.rerun()
        else:
            st.info("Belum ada jawaban essay dari siswa yang dikumpulkan.")

# ==========================================
# 8. PANEL SISWA
# ==========================================
elif role == "siswa":
    st.title("🇮🇩 Ruang Siswa")
    menu_s = st.sidebar.radio(
        "📌 Menu Siswa",
        ["📚 Modul Materi", "✍️ Kerjakan Tugas", "📊 Riwayat & Nilai Saya"]
    )

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
        tugas_list = [{"id": d.id, **d.to_dict()} for d in docs]

        if tugas_list:
            tugas_map = {t["id"]: f"[{'PG' if t.get('tipe')=='pg' else 'ESSAY'}] {t.get('judul')}" for t in tugas_list}
            selected_tugas_id = st.selectbox("Pilih Tugas yang Ingin Dikerjakan", list(tugas_map.keys()), format_func=lambda x: tugas_map[x])
            
            tugas_active = next(t for t in tugas_list if t["id"] == selected_tugas_id)
            
            cek_jwb = db.collection("jawaban_siswa")\
                .where("id_tugas", "==", selected_tugas_id)\
                .where("username_siswa", "==", user_info["username"]).get()

            if len(cek_jwb) > 0:
                st.warning("⚠️ Anda sudah pernah mengumpulkan tugas ini! Silakan cek nilai di menu 'Riwayat & Nilai Saya'.")
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
                            st.write("")
                        
                        btn_sub_pg = st.form_submit_button("Kumpulkan Jawaban PG")

                        if btn_sub_pg:
                            benar = 0
                            total_soal = len(tugas_active.get("soal", []))
                            for idx, s in enumerate(tugas_active.get("soal", [])):
                                if jawaban_pg[idx] == s.get("kunci"):
                                    benar += 1
                            
                            nilai_pg = round((benar / total_soal) * 100)
                            
                            db.collection("jawaban_siswa").add({
                                "id_tugas": selected_tugas_id,
                                "judul_tugas": tugas_active.get("judul"),
                                "username_siswa": user_info["username"],
                                "nama_siswa": user_info["nama"],
                                "tipe": "pg",
                                "jawaban": jawaban_pg,
                                "nilai": nilai_pg,
                                "catatan_guru": "Diperiksa otomatis oleh sistem",
                                "submitted_at": firestore.SERVER_TIMESTAMP
                            })
                            st.success(f"Tugas dikumpulkan! Nilai Anda: {nilai_pg}/100")
                            st.rerun()

                else:  # Essay
                    with st.form("form_kerjakan_essay"):
                        jawaban_essay = []
                        for i, q in enumerate(tugas_active.get("soal", []), 1):
                            st.markdown(f"**{i}. {q}**")
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
                                    "tipe": "essay",
                                    "soal": tugas_active.get("soal"),
                                    "jawaban": jawaban_essay,
                                    "nilai": None,
                                    "catatan_guru": "",
                                    "submitted_at": firestore.SERVER_TIMESTAMP
                                })
                                st.success("Jawaban Essay dikumpulkan! Silakan tunggu pemeriksaan dari Guru.")
                                st.rerun()
                            else:
                                st.warning("Mohon isi seluruh pertanyaan essay sebelum mengumpulkan!")
        else:
            st.info("Belum ada tugas yang dipublikasikan oleh Guru.")

    # --- 8C. RIWAYAT & NILAI SAYA ---
    elif menu_s == "📊 Riwayat & Nilai Saya":
        st.header("📊 Riwayat Tugas & Hasil Nilai")
        docs = db.collection("jawaban_siswa").where("username_siswa", "==", user_info["username"]).stream()
        hasil_list = [{"id": d.id, **d.to_dict()} for d in docs]

        if hasil_list:
            for h in hasil_list:
                status_n = f"💯 Nilai: {h.get('nilai')}" if h.get("nilai") is not None else "⏳ Belum Dinilai Guru"
                with st.expander(f"📋 {h.get('judul_tugas')} [{h.get('tipe').upper()}] — {status_n}"):
                    if h.get("catatan_guru"):
                        st.info(f"**Catatan/Feedback Guru:** {h.get('catatan_guru')}")
                    st.write("**Detail Pengerjaan:**")
                    if h.get("tipe") == "essay":
                        for s_q, s_a in zip(h.get("soal", []), h.get("jawaban", [])):
                            st.markdown(f"• **Soal:** {s_q}")
                            st.caption(f"  *Jawaban Anda:* {s_a}")
                    else:
                        st.write("Tugas Pilihan Ganda telah selesai diperiksa otomatis.")
        else:
            st.info("Anda belum pernah mengumpulkan tugas apapun.")
