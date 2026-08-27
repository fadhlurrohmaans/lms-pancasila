# ==========================================
# 8. PANEL SISWA (ANTI-CHEAT HIDDEN BUTTON)
# ==========================================
def render_siswa():
    kelas_s = user_info.get("kelas", "-")
    nama_s = user_info.get("nama", "Siswa")
    username_s = user_info.get("username", "")
    active_quiz_id = st.session_state.get("active_quiz_id")

    # CSS Khusus: Menyembunyikan tombol trigger anti-cheat dari layar siswa
    st.markdown("""
        <style>
        div[data-testid="stButton"]:has(button[aria-label*="Catat Pelanggaran"]) {
            display: none !important;
        }
        </style>
    """, unsafe_allow_html=True)

    # ------------------------------------------
    # ISOLATION MODE: HANYA RENDER KUIS (JIKA AKTIF)
    # ------------------------------------------
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
        soal_list = tg.get("soal", [])
        total_soal = len(soal_list)

        # 1. INISIALISASI STATE UJIAN & ANTI-CHEAT
        if f"quiz_loaded_{tg_id}" not in st.session_state:
            my_statuses = get_user_statuses_cached(username_s)
            status_data = my_statuses.get(tg_id, {})
            has_status = bool(status_data)

            draft_ans = status_data.get("draft_answers")
            if draft_ans and isinstance(draft_ans, list) and len(draft_ans) == total_soal:
                st.session_state[f"quiz_answers_{tg_id}"] = draft_ans
            else:
                st.session_state[f"quiz_answers_{tg_id}"] = [None] * total_soal

            if is_ulangan:
                if not has_status:
                    db.collection("status_ujian").document(f"{username_s}_{tg_id}").set({
                        "username": username_s, "id_tugas": tg_id, "status": "in_progress",
                        "ijin_guru": False, "violation_count": 0, "updated_at": firestore.SERVER_TIMESTAMP
                    }, merge=True)
                    clear_user_statuses_cache()
                    st.session_state[f"ijin_guru_{tg_id}"] = True
                elif status_data.get("status") == "in_progress":
                    ijin_db = status_data.get("ijin_guru", False)
                    st.session_state[f"ijin_guru_{tg_id}"] = ijin_db
                    if ijin_db:
                        db.collection("status_ujian").document(f"{username_s}_{tg_id}").set({"ijin_guru": False}, merge=True)
                        clear_user_statuses_cache()
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

        # 2. TRIGGER PELANGGARAN TERSEMBUNYI & JS DETECTION
        if is_ulangan:
            # Tombol ini disembunyikan oleh CSS di atas tetapi tetap bisa diklik oleh JS
            if st.button("⚠️ Catat Pelanggaran", key=f"btn_rec_viol_{tg_id}", type="secondary"):
                if not is_locked:
                    doc_ref = db.collection("status_ujian").document(f"{username_s}_{tg_id}")
                    doc_ref.set({
                        "username": username_s, 
                        "id_tugas": tg_id, 
                        "violation_count": firestore.Increment(1),
                        "status": "in_progress", 
                        "updated_at": firestore.SERVER_TIMESTAMP
                    }, merge=True)
                    clear_user_statuses_cache()
                    
                    st.session_state[f"violation_count_{tg_id}"] += 1
                    new_v = st.session_state[f"violation_count_{tg_id}"]

                    if new_v >= 15:
                        tg_sub = dict(tg)
                        tg_sub["soal"] = soal_list
                        submit_jawaban_siswa(tg_sub, username_s, nama_s, kelas_s, answers, is_violation=True)
                        
                        st.session_state["active_quiz_id"] = None
                        for k in [f"active_quiz_data", f"quiz_answers_{tg_id}", f"quiz_page_{tg_id}", f"quiz_loaded_{tg_id}", f"ac_rendered_{tg_id}"]:
                            st.session_state.pop(k, None)
                        st.error("🚨 Kuis otomatis dikumpulkan karena mencapai 15 kali pelanggaran!")
                        st.rerun()
                    else:
                        st.rerun()

            # Injeksi JS Anti-Cheat (menggunakan textContent/aria-label agar tetap menemukan tombol yang tersembunyi)
            if not is_locked and not st.session_state.get(f"ac_rendered_{tg_id}", False):
                components.html("""
                    <script>
                    (function() {
                        const parentDoc = window.parent.document;
                        let lastTrigger = 0;

                        function triggerViolation() {
                            const now = Date.now();
                            if (now - lastTrigger < 3000) return;
                            lastTrigger = now;

                            const buttons = Array.from(parentDoc.querySelectorAll('button'));
                            const triggerBtn = buttons.find(b => {
                                const label = b.getAttribute('aria-label') || b.textContent || '';
                                return label.includes('Catat Pelanggaran');
                            });

                            if (triggerBtn) {
                                triggerBtn.click();
                            }
                        }

                        if (!window.parent._antiCheatInitialized) {
                            window.parent._antiCheatInitialized = true;
                            parentDoc.addEventListener('visibilitychange', function() {
                                if (parentDoc.hidden) triggerViolation();
                            });
                            window.parent.addEventListener('blur', function() {
                                triggerViolation();
                            });
                        }
                    })();
                    </script>
                """, height=0)
                st.session_state[f"ac_rendered_{tg_id}"] = True

        # 3. TAMPILAN JIKA ULANGAN TERKUNCI
        if is_locked:
            st.error("🔒 **ULANGAN HARIAN TERKUNCI**: Anda terdeteksi **keluar/berpindah layar/refresh** dari kuis. Silakan minta izin ke Guru untuk membuka kuis.")
            if st.button("🔄 Cek Status Izin Guru", key=f"btn_check_perm_{tg_id}", type="primary"):
                clear_user_statuses_cache()
                fresh_statuses = get_user_statuses_cached(username_s)
                status_doc_data = fresh_statuses.get(tg_id, {})
                ijin_val = status_doc_data.get("ijin_guru", False)
                st.session_state[f"ijin_guru_{tg_id}"] = ijin_val
                if ijin_val:
                    db.collection("status_ujian").document(f"{username_s}_{tg_id}").set({"ijin_guru": False}, merge=True)
                    clear_user_statuses_cache()
                st.rerun()
            return

        if is_ulangan and violation_count > 0:
            st.warning(f"⚠️ **PERINGATAN PELANGGARAN ({violation_count}/15)**: Terdeteksi pernah keluar dari layar kuis!")

        # 4. HEADER KUIS
        c_h1, c_h2 = st.columns([3, 1])
        with c_h1:
            st.markdown(f"### 📝 {tg.get('judul')}")
            st.caption(f"Terjawab: **{terjawab_count}/{total_soal}** | Nomor: **{curr_page + 1}/{total_soal}**")
        with c_h2:
            if st.button("⬅️ Keluar", key="btn_exit_quiz", type="secondary", use_container_width=True):
                save_draft_to_firebase(username_s, tg_id, answers)
                if is_ulangan:
                    db.collection("status_ujian").document(f"{username_s}_{tg_id}").set({"ijin_guru": False}, merge=True)
                    clear_user_statuses_cache()
                st.session_state["active_quiz_id"] = None
                st.session_state.pop("active_quiz_data", None)
                st.session_state.pop(f"quiz_loaded_{tg_id}", None)
                st.rerun()

        st.progress((curr_page + 1) / total_soal)

        # 5. NAVIGASI NUMERIK SELEKTIF
        c_prev, c_jump, c_next = st.columns([1, 2, 1])
        with c_prev:
            if curr_page > 0:
                if st.button("⬅️ Sebelum", key="nav_prev_btn", use_container_width=True):
                    st.session_state[f"quiz_page_{tg_id}"] = curr_page - 1
                    st.rerun()

        with c_jump:
            jump_options = list(range(total_soal))
            selected_no = st.selectbox(
                "Nomor Soal",
                options=jump_options,
                index=curr_page,
                format_func=lambda i: f"{'🟢' if answers[i] is not None and str(answers[i]).strip() != '' else '⚪'} Soal No. {i + 1}",
                key=f"sb_jump_{tg_id}",
                label_visibility="collapsed"
            )
            if selected_no != curr_page:
                st.session_state[f"quiz_page_{tg_id}"] = selected_no
                st.rerun()

        with c_next:
            if curr_page < total_soal - 1:
                if st.button("Lanjut ➡️", key="nav_next_btn", type="primary", use_container_width=True):
                    st.session_state[f"quiz_page_{tg_id}"] = curr_page + 1
                    st.rerun()

        st.divider()

        # 6. RENDER DOKUMEN SOAL
        soal_item = soal_list[curr_page]
        q_text = soal_item.get("pertanyaan") if isinstance(soal_item, dict) else str(soal_item)

        st.markdown(f"#### Soal No. {curr_page + 1}")
        st.markdown(f"**{q_text}**")

        raw_ans = answers[curr_page]

        if tg.get("tipe") == "pg":
            opsi_list = soal_item.get("opsi", [])
            safe_index = None
            if raw_ans is not None:
                try:
                    parsed_idx = int(raw_ans)
                    if 0 <= parsed_idx < len(opsi_list):
                        safe_index = parsed_idx
                except (ValueError, TypeError):
                    safe_index = None

            selected_opt = st.radio(
                "Pilih Jawaban Anda:",
                options=list(range(len(opsi_list))),
                index=safe_index,
                format_func=lambda x: f"{['A','B','C','D'][x]}. {opsi_list[x]}",
                key=f"radio_ans_{tg_id}_{curr_page}"
            )

            if selected_opt != raw_ans:
                answers[curr_page] = selected_opt
                st.session_state[f"quiz_answers_{tg_id}"] = answers
        else:
            saved_text = str(raw_ans) if raw_ans is not None else ""
            essay_text = st.text_area(
                "Jawaban Anda:",
                value=saved_text,
                height=140,
                key=f"text_ans_{tg_id}_{curr_page}"
            )
            if essay_text != saved_text:
                answers[curr_page] = essay_text if essay_text.strip() else None
                st.session_state[f"quiz_answers_{tg_id}"] = answers

        st.divider()

        # 7. TOMBOL SUBMIT UTAMA
        if st.button("🚀 Kumpulkan Semua Jawaban", key="btn_final_submit", type="primary", use_container_width=True):
            tg_submit = dict(tg)
            tg_submit["soal"] = soal_list
            with st.spinner("Mengirim jawaban..."):
                success = submit_jawaban_siswa(tg_submit, username_s, nama_s, kelas_s, answers, is_forced=False)
            if success:
                st.balloons()
                st.success("✅ Jawaban berhasil dikumpulkan!")
                st.session_state["active_quiz_id"] = None
                for k in ["active_quiz_data", f"quiz_answers_{tg_id}", f"quiz_page_{tg_id}", f"quiz_soal_{tg_id}", f"quiz_loaded_{tg_id}", f"ac_rendered_{tg_id}"]:
                    st.session_state.pop(k, None)
                st.rerun()

        return

    # ------------------------------------------
    # DASHBOARD UTAMA SISWA (JIKA TIDAK SEDANG KUIS)
    # ------------------------------------------
    my_subs = get_user_submissions_cached(username_s)
    my_statuses = get_user_statuses_cached(username_s)
    
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
                    
                    st_data = my_statuses.get(tg['id'], {})
                    has_draft = bool(st_data.get("draft_answers"))
                    
                    btn_label = "▶️ Lanjutkan Pengerjaan" if has_draft else "🚀 Mulai Kerjakan"
                    if st.button(btn_label, key=f"start_{tg['id']}", type="primary"):
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
