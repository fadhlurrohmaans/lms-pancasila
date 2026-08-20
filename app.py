import streamlit as st
import firebase_admin
from firebase_admin import credentials, firestore
import pandas as pd
import hashlib
import re
import random
import string
import json
import google.generativeai as genai

# ==========================================
# 1. CONFIG & STYLING
# ==========================================
st.set_page_config(
    page_title="LMS Pendidikan Pancasila",
    page_icon="🇮🇩",
    layout="wide",
    initial_sidebar_state="auto"
)

st.markdown("""
    <style>
    input[type="text"], input[type="password"], textarea, select { font-size: 16px !important; }
    @media (max-width: 768px) {
        .main .block-container { padding: 1rem 0.8rem 2rem !important; }
        [data-testid="column"] { width: 100% !important; flex: 1 1 100% !important; min-width: 100% !important; margin-bottom: 0.5rem; }
        .stButton > button, .stDownloadButton > button { width: 100% !important; min-height: 48px !important; font-size: 16px !important; font-weight: bold; border-radius: 8px !important; }
        h1 { font-size: 1.8rem !important; } h2 { font-size: 1.4rem !important; } h3 { font-size: 1.2rem !important; }
    }
    .stTabs [data-baseweb="tab-list"] { gap: 8px; overflow-x: auto; white-space: nowrap; }
    .stTabs [data-baseweb="tab"] { padding: 8px 16px; }
    </style>
""", unsafe_allow_html=True)

# ==========================================
# 2. FIREBASE & CACHED HELPERS
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

@st.cache_data(ttl=60)
def get_all_kelas():
    docs = db.collection("kelas").stream()
    return sorted([d.id for d in docs])

def clear_kelas_cache():
    get_all_kelas.clear()

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
    base_username = re.sub(r'[^a-z0-9]', '', nama.lower()) or "siswa"
    username, counter = base_username, 1
    while db.collection("users").document(username).get().exists:
        username = f"{base_username}{counter}"
        counter += 1
    return username

def generate_password(length=6):
    return ''.join(random.choices(string.ascii_lowercase + string.digits, k=length))

def is_tugas_sesuai_kelas(tugas_doc, kelas_siswa):
    target = tugas_doc.get("target_kelas", [])
    if not target: return True
    return kelas_siswa in target if isinstance(target, list) else target == kelas_siswa
# ==========================================
# 3. AI EVALUATION HELPER (EXTRACTION & PARSING FIX)
# ==========================================
def koreksi_essay_dengan_ai(soal_list, jawaban_list):
    api_key = st.secrets.get("GEMINI_API_KEY") or st.secrets.get("gemini", {}).get("api_key") or st.secrets.get("firebase", {}).get("GEMINI_API_KEY")
    if not api_key:
        return None, "⚠️ Key 'GEMINI_API_KEY' belum dikonfigurasi di secrets Streamlit."

    try:
        genai.configure(api_key=api_key)
        
        # 1. Pilih model yang tersedia secara dinamis
        candidate_models = ['gemini-1.5-flash', 'gemini-2.0-flash', 'models/gemini-1.5-flash']
        try:
            active_models = [
                m.name for m in genai.list_models() 
                if 'generateContent' in m.supported_generation_methods
            ]
            for m in reversed(active_models):
                if m not in candidate_models:
                    candidate_models.insert(0, m)
        except Exception:
            pass

        # 2. Susun Prompt
        prompt_items = [
            f"Soal No.{i}: {s.get('pertanyaan', '') if isinstance(s, dict) else str(s)}\nJawaban Siswa No.{i}: {j if j and str(j).strip() else '(Kosong)'}\n"
            for i, (s, j) in enumerate(zip(soal_list, jawaban_list), 1)
        ]

        prompt = f"""
        Anda adalah Guru Pendidikan Pancasila. Evaluasi jawaban essay siswa berikut secara obyektif.
        
        {chr(10).join(prompt_items)}

        WAJIB kembalikan JSON valid tanpa teks pengantar apapun. Format:
        {{"nilai": 85, "feedback": "Penjelasan sudah baik dan sesuai konsep Sila Pancasila."}}
        """

        # 3. Panggil API AI
        response = None
        last_error = None

        for model_name in candidate_models:
            try:
                model = genai.GenerativeModel(model_name)
                try:
                    response = model.generate_content(prompt, generation_config={"response_mime_type": "application/json"})
                except Exception:
                    response = model.generate_content(prompt)
                
                if response and hasattr(response, 'text') and response.text and response.text.strip():
                    break
            except Exception as err:
                last_error = err
                continue

        if not response or not hasattr(response, 'text') or not response.text.strip():
            return None, f"AI tidak mengembalikan respon teks. Error terakhir: {str(last_error)}"

        raw_text = response.text.strip()

        # 4. Ekstrak JSON menggunakan Regex agar aman dari teks tambahan/markdown
        json_match = re.search(r'\{.*\}', raw_text, re.DOTALL)
        if json_match:
            json_str = json_match.group(0)
            result_json = json.loads(json_str)
            return int(result_json.get("nilai", 0)), str(result_json.get("feedback", ""))
        else:
            return None, f"Respon AI bukan format JSON valid: {raw_text[:100]}"

    except json.JSONDecodeError:
        return None, f"Gagal membaca JSON AI. Raw Output: {raw_text[:100]}"
    except Exception as e:
        return None, f"Gagal mengeksekusi AI: {str(e)}"
        # 4. Eksekusi panggilan model secara berurutan
        response = None
        last_error = None

        for model_name in candidate_models:
            try:
                model = genai.GenerativeModel(model_name)
                try:
                    response = model.generate_content(prompt, generation_config={"response_mime_type": "application/json"})
                except Exception:
                    response = model.generate_content(prompt)
                
                if response and response.text:
                    break
            except Exception as err:
                last_error = err
                continue

        if not response or not response.text:
            return None, f"Gagal mengeksekusi AI pada seluruh model. Error terakhir: {str(last_error)}"

        # 5. Parsing JSON keluaran AI
        raw_text = response.text.strip()
        if raw_text.startswith("```"):
            raw_text = re.sub(r"^```(?:json)?|```$", "", raw_text, flags=re.MULTILINE).strip()
            
        result_json = json.loads(raw_text)
        return int(result_json.get("nilai", 0)), str(result_json.get("feedback", ""))
        
    except Exception as e:
        return None, f"Gagal mengeksekusi AI: {str(e)}"
# ==========================================
# 4. AUTHENTICATION
# ==========================================
if "user" not in st.session_state:
    st.session_state["user"] = None

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
    st.stop()

# ==========================================
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
    st.session_state["user"] = None
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
        users = [
            {
                "Username": d.id,
                "Nama": (u := d.to_dict()).get("nama"),
                "Role": u.get("role", "").upper(),
                "Kelas": u.get("kelas", "-") if u.get("role") == "siswa" else ", ".join(u.get("kelas_ajar", []))
            } for d in db.collection("users").stream()
        ]
        if users: st.dataframe(pd.DataFrame(users), use_container_width=True)

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
        st.subheader("📥 Import & 📤 Export Siswa")
        col_imp, col_exp = st.columns(2)
        with col_imp:
            up_file = st.file_uploader("Unggah File Siswa (.csv / .xlsx)", type=["csv", "xlsx"])
            if up_file and st.button("🚀 Import Siswa", type="primary"):
                df = safe_read_uploaded_file(up_file)
                df.columns = [str(c).strip().lower() for c in df.columns]
                if "nama" in df.columns and "kelas" in df.columns:
                    exist_map = {d.to_dict().get("nama", "").strip().lower(): d.id for d in db.collection("users").where("role", "==", "siswa").stream()}
                    c_new, c_up = 0, 0
                    for _, r in df.iterrows():
                        n_str, k_str = str(r["nama"]).strip(), str(r["kelas"]).strip()
                        if not n_str or pd.isna(r["nama"]): continue
                        
                        n_key = n_str.lower()
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
                    st.success(f"✅ Selesai: {c_new} baru, {c_up} diperbarui.")
                    st.rerun()

        with col_exp:
            data_siswa = [
                {"Nama": (u := d.to_dict()).get("nama"), "Username": d.id, "Password": u.get("password_plain", "*****"), "Kelas": u.get("kelas", "")}
                for d in db.collection("users").where("role", "==", "siswa").stream()
            ]
            if data_siswa:
                df_exp = pd.DataFrame(data_siswa)
                st.download_button("💾 Unduh CSV Data Siswa", df_exp.to_csv(index=False).encode('utf-8'), "data_siswa.csv", "text/csv")

    with t_edit:
        st.subheader("✏️ Atur Kelas User")
        docs = db.collection("users").stream()
        users_map = {d.id: f"{d.to_dict().get('nama')} (@{d.id})" for d in docs if d.to_dict().get("role") in ["siswa", "guru"]}
        daftar_k = get_all_kelas()
        if users_map and daftar_k:
            target_uid = st.selectbox("Pilih Pengguna", list(users_map.keys()), format_func=lambda x: users_map[x])
            u_data = db.collection("users").document(target_uid).get().to_dict()
            
            with st.form("form_edit_user_k"):
                if u_data.get("role") == "siswa":
                    new_k = st.selectbox("Kelas Baru", options=daftar_k)
                    if st.form_submit_button("Simpan"):
                        db.collection("users").document(target_uid).update({"kelas": new_k})
                        st.success("✅ Berhasil diupdate!")
                        st.rerun()
                else:
                    new_ka = st.multiselect("Kelas Ajar Baru", options=daftar_k)
                    if st.form_submit_button("Simpan"):
                        db.collection("users").document(target_uid).update({"kelas_ajar": new_ka})
                        st.success("✅ Berhasil diupdate!")
                        st.rerun()

    with t_del:
        st.subheader("🗑️ Hapus Akun")
        all_u = {d.id: f"{d.to_dict().get('nama')} (@{d.id})" for d in db.collection("users").stream() if d.id != user_info["username"]}
        if all_u:
            target_del = st.selectbox("Pilih Akun", list(all_u.keys()), format_func=lambda x: all_u[x])
            if st.button("Hapus Akun", type="primary"):
                db.collection("users").document(target_del).delete()
                st.success("✅ Akun berhasil dihapus!")
                st.rerun()

# ==========================================
# 7. PANEL GURU
# ==========================================
def render_guru():
    st.title("🇮🇩 Panel Guru")
    menu = st.sidebar.radio("📌 Menu Guru", ["📖 Kelola Materi", "📝 Buat & Kelola Tugas", "📊 Rekap & Penilaian"])
    pilihan_kelas = user_info.get("kelas_ajar") or get_all_kelas()
    if isinstance(pilihan_kelas, str): pilihan_kelas = [pilihan_kelas]

    if menu == "📖 Kelola Materi":
        st.header("📖 Kelola Materi")
        t1, t2, t3 = st.tabs(["📋 Daftar Materi", "➕ Tambah", "🗑️ Hapus"])
        
        with t1:
            for m in [d.to_dict() for d in db.collection("materi_pancasila").stream()]:
                with st.expander(f"📌 [{m.get('bab')}] {m.get('judul')}"): st.write(m.get("konten"))
        
        with t2:
            with st.form("f_mat"):
                bab, judul, konten = st.text_input("Bab"), st.text_input("Judul"), st.text_area("Konten")
                if st.form_submit_button("Simpan Materi") and bab and judul and konten:
                    db.collection("materi_pancasila").add({"bab": bab, "judul": judul, "konten": konten, "created_at": firestore.SERVER_TIMESTAMP})
                    st.success("✅ Materi disimpan!")
                    st.rerun()

        with t3:
            mats = {d.id: f"[{d.to_dict().get('bab')}] {d.to_dict().get('judul')}" for d in db.collection("materi_pancasila").stream()}
            if mats:
                target_m = st.selectbox("Pilih Materi", list(mats.keys()), format_func=lambda x: mats[x])
                if st.button("Hapus Materi", type="primary"):
                    db.collection("materi_pancasila").document(target_m).delete()
                    st.success("✅ Materi dihapus!")
                    st.rerun()

    elif menu == "📝 Buat & Kelola Tugas":
        st.header("📝 Buat & Kelola Tugas")
        t_list, t_buat, t_edit, t_imp = st.tabs(["📋 Daftar", "➕ Buat Tugas", "✏️ Edit Tugas", "📥 Import Soal"])

        with t_list:
            for tg in [{"id": d.id, **d.to_dict()} for d in db.collection("tugas_pancasila").stream()]:
                target_str = ", ".join(tg.get("target_kelas", [])) if tg.get("target_kelas") else "Semua"
                with st.expander(f"[{'PG' if tg.get('tipe')=='pg' else 'Essay'}] {tg.get('judul')} (Kelas: {target_str})"):
                    st.write(f"**Instruksi:** {tg.get('instruksi')}")
                    st.write(f"**Jumlah Soal:** {len(tg.get('soal', []))}")
                    if st.button(f"🗑️ Hapus Tugas", key=f"del_{tg['id']}"):
                        db.collection("tugas_pancasila").document(tg["id"]).delete()
                        st.success("✅ Berhasil! Tugas telah dihapus.")
                        st.rerun()

        with t_buat:
            judul = st.text_input("Judul Tugas")
            instruksi = st.text_area("Instruksi")
            target_k = st.multiselect("Target Kelas", options=pilihan_kelas, default=pilihan_kelas)
            tipe_t = st.radio("Tipe Soal", ["Pilihan Ganda", "Essay"])

            if tipe_t == "Pilihan Ganda":
                n_soal = st.number_input("Jumlah Soal", 1, 20, 2)
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
                                "soal": soal_list, "created_at": firestore.SERVER_TIMESTAMP
                            })
                            st.success("✅ Berhasil! Tugas Pilihan Ganda berhasil diterbitkan.")
                            st.rerun()
            else:
                n_essay = st.number_input("Jumlah Soal Essay", 1, 10, 2)
                with st.form("form_essay"):
                    soal_list = [{"pertanyaan": st.text_area(f"Soal #{i+1}", key=f"qe_{i}")} for i in range(n_essay)]
                    if st.form_submit_button("Simpan Tugas Essay"):
                        if judul and target_k:
                            db.collection("tugas_pancasila").add({
                                "judul": judul, "instruksi": instruksi, "tipe": "essay", "target_kelas": target_k,
                                "soal": soal_list, "created_at": firestore.SERVER_TIMESTAMP
                            })
                            st.success("✅ Berhasil! Tugas Essay berhasil diterbitkan.")
                            st.rerun()

        with t_edit:
            st.subheader("✏️ Edit Tugas & Soal")
            tugas_list = [{"id": d.id, **d.to_dict()} for d in db.collection("tugas_pancasila").stream()]
            if tugas_list:
                tg_map = {t["id"]: f"[{t.get('tipe', '').upper()}] {t.get('judul')}" for t in tugas_list}
                sel_id = st.selectbox("Pilih Tugas yang Akan Diedit", list(tg_map.keys()), format_func=lambda x: tg_map[x])
                target_tg = next(t for t in tugas_list if t["id"] == sel_id)

                with st.form("form_update_tg"):
                    e_judul = st.text_input("Judul Tugas", value=target_tg.get("judul", ""))
                    e_instruksi = st.text_area("Instruksi Tugas", value=target_tg.get("instruksi", ""))
                    e_target = st.multiselect("Target Kelas", options=pilihan_kelas, default=target_tg.get("target_kelas", pilihan_kelas))
                    
                    st.divider()
                    st.write("📝 **Daftar Soal Tugas:**")
                    
                    tipe_tugas = target_tg.get("tipe", "pg")
                    existing_soal = target_tg.get("soal", [])
                    updated_soal = []

                    if tipe_tugas == "pg":
                        for i, s in enumerate(existing_soal):
                            st.markdown(f"**Soal #{i+1}**")
                            q_val = s.get("pertanyaan", "") if isinstance(s, dict) else str(s)
                            e_q = st.text_area(f"Pertanyaan #{i+1}", value=q_val, key=f"e_q_{i}")
                            
                            opsi = s.get("opsi", ["", "", "", ""]) if isinstance(s, dict) else ["", "", "", ""]
                            c1, c2 = st.columns(2)
                            e_o0 = c1.text_input(f"A #{i+1}", value=opsi[0] if len(opsi)>0 else "", key=f"e_a_{i}")
                            e_o1 = c1.text_input(f"B #{i+1}", value=opsi[1] if len(opsi)>1 else "", key=f"e_b_{i}")
                            e_o2 = c2.text_input(f"C #{i+1}", value=opsi[2] if len(opsi)>2 else "", key=f"e_c_{i}")
                            e_o3 = c2.text_input(f"D #{i+1}", value=opsi[3] if len(opsi)>3 else "", key=f"e_d_{i}")
                            
                            curr_k = s.get("kunci", 0) if isinstance(s, dict) else 0
                            curr_idx = int(curr_k) if isinstance(curr_k, int) and 0 <= int(curr_k) <= 3 else 0
                            e_k = st.selectbox(f"Kunci Jawaban #{i+1}", [0, 1, 2, 3], index=curr_idx, format_func=lambda x: ['A','B','C','D'][x], key=f"e_k_{i}")
                            
                            updated_soal.append({"pertanyaan": e_q, "opsi": [e_o0, e_o1, e_o2, e_o3], "kunci": e_k})
                    else:
                        for i, s in enumerate(existing_soal):
                            q_val = s.get("pertanyaan", "") if isinstance(s, dict) else str(s)
                            e_q = st.text_area(f"Soal Essay #{i+1}", value=q_val, key=f"e_qe_{i}")
                            updated_soal.append({"pertanyaan": e_q})

                    if st.form_submit_button("💾 Perbarui Tugas & Soal"):
                        db.collection("tugas_pancasila").document(sel_id).update({
                            "judul": e_judul,
                            "instruksi": e_instruksi,
                            "target_kelas": e_target,
                            "soal": updated_soal,
                            "updated_at": firestore.SERVER_TIMESTAMP
                        })
                        st.success("✅ Berhasil! Informasi tugas dan soal telah diperbarui.")
                        st.rerun()

        with t_imp:
            st.subheader("📥 Import Soal Tugas")
            st.write("💡 **Unduh Template CSV:** Silakan unduh format template di bawah ini sebelum mengunggah file soal.")
            
            csv_pg_example = "pertanyaan,opsi_a,opsi_b,opsi_c,opsi_d,kunci\nSila pertama Pancasila dilambangkan oleh?,Bintang,Rantai,Pohon Beringin,Banteng,A\n"
            csv_essay_example = "pertanyaan\nJelaskan makna Sila ke-3 Pancasila bagi persatuan bangsa!\n"

            c_tmp1, c_tmp2 = st.columns(2)
            with c_tmp1:
                st.download_button("📄 Unduh Template PG (.csv)", csv_pg_example.encode('utf-8'), "template_soal_pg.csv", "text/csv")
            with c_tmp2:
                st.download_button("📄 Unduh Template Essay (.csv)", csv_essay_example.encode('utf-8'), "template_soal_essay.csv", "text/csv")

            st.divider()

            up_soal = st.file_uploader("Upload File Soal (.csv / .xlsx)", type=["csv", "xlsx"])
            imp_judul = st.text_input("Judul Tugas Baru")
            imp_instruksi = st.text_area("Instruksi (Opsional)")
            imp_target = st.multiselect("Target Kelas Import", options=pilihan_kelas, default=pilihan_kelas)
            imp_tipe = st.selectbox("Tipe Soal Import", ["pg", "essay"])

            if up_soal and imp_judul and imp_target and st.button("🚀 Import Soal Sekarang", type="primary"):
                df_s = safe_read_uploaded_file(up_soal)
                df_s.columns = [str(c).strip().lower() for c in df_s.columns]
                parsed_s = []
                
                if imp_tipe == "pg":
                    key_m = {'a':0, 'b':1, 'c':2, 'd':3, '0':0, '1':1, '2':2, '3':3}
                    for _, r in df_s.iterrows():
                        parsed_s.append({
                            "pertanyaan": str(r["pertanyaan"]),
                            "opsi": [str(r["opsi_a"]), str(r["opsi_b"]), str(r["opsi_c"]), str(r["opsi_d"])],
                            "kunci": key_m.get(str(r["kunci"]).strip().lower(), 0)
                        })
                else:
                    for _, r in df_s.iterrows():
                        parsed_s.append({"pertanyaan": str(r["pertanyaan"])})

                db.collection("tugas_pancasila").add({
                    "judul": imp_judul, "instruksi": imp_instruksi, "tipe": imp_tipe, "target_kelas": imp_target,
                    "soal": parsed_s, "created_at": firestore.SERVER_TIMESTAMP
                })
                st.success("✅ Berhasil! Soal berhasil diimpor ke database.")
                st.rerun()

    elif menu == "📊 Rekap & Penilaian":
        st.header("📊 Rekap & Penilaian Tugas Per Kelas")
        
        if not pilihan_kelas:
            st.warning("⚠️ Anda belum ditugaskan untuk mengajar di kelas manapun.")
            st.stop()

        col_k, col_t = st.columns(2)
        with col_k:
            selected_kelas = st.selectbox("🏫 Pilih Kelas Ajar", options=pilihan_kelas)

        all_tugas_docs = db.collection("tugas_pancasila").stream()
        tugas_kelas = [
            {"id": d.id, **d.to_dict()} 
            for d in all_tugas_docs 
            if is_tugas_sesuai_kelas(d.to_dict(), selected_kelas)
        ]

        if not tugas_kelas:
            st.info(f"Belum ada tugas yang ditujukan untuk Kelas **{selected_kelas}**.")
            st.stop()

        with col_t:
            tg_options = {t["id"]: f"[{t.get('tipe', '').upper()}] {t.get('judul')}" for t in tugas_kelas}
            selected_tugas_id = st.selectbox("📝 Pilih Tugas", list(tg_options.keys()), format_func=lambda x: tg_options[x])
            selected_tugas = next(t for t in tugas_kelas if t["id"] == selected_tugas_id)

        # Query Siswa di Kelas Terpilih
        siswa_docs = db.collection("users").where("role", "==", "siswa").where("kelas", "==", selected_kelas).stream()
        siswa_list = [{"username": d.id, **d.to_dict()} for d in siswa_docs]

        # Query Jawaban Siswa
        sub_docs = db.collection("jawaban_siswa").where("id_tugas", "==", selected_tugas_id).where("kelas_siswa", "==", selected_kelas).stream()
        sub_list = [{"id": d.id, **d.to_dict()} for d in sub_docs]
        sub_map = {s.get("username_siswa"): s for s in sub_list}

        # Kalkulasi Rekap Status
        rekap_rows = []
        sudah_count, belum_count = 0, 0

        for s in siswa_list:
            un = s["username"]
            nama = s.get("nama", un)
            if un in sub_map:
                sudah_count += 1
                sub = sub_map[un]
                val_display = sub.get("nilai") if sub.get("nilai") is not None else "Belum Dinilai"
                rekap_rows.append({
                    "Username": un,
                    "Nama Siswa": nama,
                    "Status": "✅ Sudah",
                    "Nilai": val_display,
                    "Catatan Guru": sub.get("catatan_guru", "-")
                })
            else:
                belum_count += 1
                rekap_rows.append({
                    "Username": un,
                    "Nama Siswa": nama,
                    "Status": "❌ Belum",
                    "Nilai": "-",
                    "Catatan Guru": "-"
                })

        m1, m2, m3 = st.columns(3)
        m1.metric("Total Siswa di Kelas", len(siswa_list))
        m2.metric("Sudah Mengumpulkan", sudah_count)
        m3.metric("Belum Mengumpulkan", belum_count)

        st.divider()

        t_rekap, t_koreksi = st.tabs(["📋 Rekap Pengerjaan Siswa", "✏️ Koreksi & Penilaian"])

        with t_rekap:
            st.subheader(f"Daftar Siswa Kelas {selected_kelas} — {selected_tugas.get('judul')}")
            if rekap_rows:
                df_rekap = pd.DataFrame(rekap_rows)
                st.dataframe(df_rekap, use_container_width=True)
            else:
                st.info("Belum ada akun siswa yang terdaftar di kelas ini.")

        # PERBAIKAN PADA PENAMPILAN & AUTO KOREKSI AI
        with t_koreksi:
            st.subheader(f"Koreksi Hasil Jawaban Kelas {selected_kelas}")
            if not sub_list:
                st.info("Belum ada siswa dari kelas ini yang mengumpulkan tugas.")
            else:
                for sub in sub_list:
                    status_str = "🟡 Belum Dinilai" if sub.get("nilai") is None else f"🟢 Nilai: {sub.get('nilai')}"
                    with st.expander(f"👤 {sub.get('nama_siswa')} (@{sub.get('username_siswa')}) — {status_str}"):
                        soal_items = sub.get("soal", selected_tugas.get("soal", []))
                        jawaban_items = sub.get("jawaban", [])

                        for idx, (q, a) in enumerate(zip(soal_items, jawaban_items), 1):
                            q_text = q.get('pertanyaan') if isinstance(q, dict) else q
                            st.write(f"**{idx}. {q_text}**")
                            
                            if sub.get("tipe") == "pg":
                                opsi_list = q.get("opsi", [])
                                ans_idx = a if isinstance(a, int) else 0
                                ans_text = opsi_list[ans_idx] if ans_idx < len(opsi_list) else str(a)
                                kunci_idx = q.get("kunci", 0)
                                is_correct = (ans_idx == kunci_idx)
                                st.write(f"Jawaban: **{ans_text}** ({'✅ Benar' if is_correct else '❌ Salah'})")
                            else:
                                st.info(a or "(Kosong)")

                        if selected_tugas.get("tipe") == "essay":
                            if st.button("🤖 Auto Koreksi AI", key=f"ai_{sub['id']}"):
                                with st.spinner("🤖 AI sedang menganalisis jawaban siswa..."):
                                    val, fb = koreksi_essay_dengan_ai(soal_items, jawaban_items)
                                if val is not None:
                                    db.collection("jawaban_siswa").document(sub["id"]).update({"nilai": val, "catatan_guru": fb})
                                    # Update session_state secara eksplisit agar widget input langsung berubah
                                    st.session_state[f"n_{sub['id']}"] = int(val)
                                    st.session_state[f"c_{sub['id']}"] = str(fb)
                                    st.success("✅ AI selesai menilai!")
                                    st.rerun()
                                else:
                                    st.error(fb)

                        with st.form(key=f"f_eval_{sub['id']}"):
                            curr_val = int(sub.get("nilai", 80)) if sub.get("nilai") is not None else 80
                            curr_cat = sub.get("catatan_guru", "")
                            
                            n_in = st.number_input("Nilai (0-100)", 0, 100, curr_val, key=f"n_{sub['id']}")
                            c_in = st.text_area("Catatan Guru", value=curr_cat, key=f"c_{sub['id']}")
                            
                            if st.form_submit_button("💾 Simpan Nilai Manual"):
                                db.collection("jawaban_siswa").document(sub["id"]).update({"nilai": n_in, "catatan_guru": c_in})
                                st.success("✅ Nilai disimpan!")
                                st.rerun()

# ==========================================
# 8. PANEL SISWA
# ==========================================
def render_siswa():
    st.title("🇮🇩 Ruang Siswa")
    menu_s = st.sidebar.radio("📌 Menu Siswa", ["📚 Modul Materi", "✍️ Kerjakan Tugas", "📊 Riwayat Nilai"])
    kelas_s = user_info.get("kelas", "")

    if menu_s == "📚 Modul Materi":
        st.header("📚 Modul Materi")
        for m in [d.to_dict() for d in db.collection("materi_pancasila").stream()]:
            with st.expander(f"📘 {m.get('bab')}: {m.get('judul')}"): st.markdown(m.get("konten"))

    elif menu_s == "✍️ Kerjakan Tugas":
        st.header("✍️ Kerjakan Tugas")
        all_tugas = [t for t in [{"id": d.id, **d.to_dict()} for d in db.collection("tugas_pancasila").stream()] if is_tugas_sesuai_kelas(t, kelas_s)]
        
        if all_tugas:
            tg_map = {t["id"]: t["judul"] for t in all_tugas}
            sel_tg_id = st.selectbox("Pilih Tugas", list(tg_map.keys()), format_func=lambda x: tg_map[x])
            tg_act = next(t for t in all_tugas if t["id"] == sel_tg_id)

            already_submitted = len(db.collection("jawaban_siswa").where("id_tugas", "==", sel_tg_id).where("username_siswa", "==", user_info["username"]).get()) > 0
            
            if already_submitted:
                st.warning("⚠️ Anda sudah mengumpulkan tugas ini.")
            else:
                st.subheader(tg_act.get("judul"))
                st.write(tg_act.get("instruksi"))
                
                with st.form("form_kerjakan"):
                    answers = []
                    for i, s in enumerate(tg_act.get("soal", []), 1):
                        if tg_act.get("tipe") == "pg":
                            st.write(f"**{i}. {s.get('pertanyaan')}**")
                            ans = st.radio("Pilih:", [0,1,2,3], format_func=lambda x, opts=s.get("opsi"): f"{['A','B','C','D'][x]}. {opts[x]}", key=f"pg_{i}")
                            answers.append(ans)
                        else:
                            st.write(f"**{i}. {s.get('pertanyaan') if isinstance(s, dict) else s}**")
                            ans = st.text_area("Jawaban:", key=f"es_{i}")
                            answers.append(ans)

                    if st.form_submit_button("Kumpulkan Tugas"):
                        if tg_act.get("tipe") == "pg":
                            score = round((sum(1 for idx, sq in enumerate(tg_act.get("soal", [])) if answers[idx] == sq.get("kunci")) / len(answers)) * 100)
                            db.collection("jawaban_siswa").add({
                                "id_tugas": sel_tg_id, "judul_tugas": tg_act.get("judul"), "username_siswa": user_info["username"],
                                "nama_siswa": user_info["nama"], "kelas_siswa": kelas_s, "tipe": "pg", "jawaban": answers,
                                "nilai": score, "catatan_guru": "Otomatis Sistem", "submitted_at": firestore.SERVER_TIMESTAMP
                            })
                            st.success(f"✅ Terkumpul! Nilai Anda: {score}")
                        else:
                            db.collection("jawaban_siswa").add({
                                "id_tugas": sel_tg_id, "judul_tugas": tg_act.get("judul"), "username_siswa": user_info["username"],
                                "nama_siswa": user_info["nama"], "kelas_siswa": kelas_s, "tipe": "essay", "soal": tg_act.get("soal"),
                                "jawaban": answers, "nilai": None, "submitted_at": firestore.SERVER_TIMESTAMP
                            })
                            st.success("✅ Terkumpul! Menunggu koreksi guru.")
                        st.rerun()

    elif menu_s == "📊 Riwayat Nilai":
        st.header("📊 Riwayat Nilai Saya")
        my_subs = [d.to_dict() for d in db.collection("jawaban_siswa").where("username_siswa", "==", user_info["username"]).stream()]
        if my_subs:
            df_my = pd.DataFrame(my_subs)
            expected_cols_siswa = ["judul_tugas", "nilai", "catatan_guru"]
            df_my_display = df_my.reindex(columns=expected_cols_siswa)
            st.dataframe(df_my_display, use_container_width=True)

# ==========================================
# 9. MAIN ROUTER
# ==========================================
if role == "superadmin":
    render_superadmin()
elif role == "guru":
    render_guru()
elif role == "siswa":
    render_siswa()
