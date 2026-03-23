#app
import streamlit as st
import pandas as pd
from supabase import create_client, Client
from datetime import datetime
import time
import os  # ← AJOUT MANQUANT

# --- CONFIGURATION DE LA PAGE ---
st.set_page_config(page_title="SkillQuest - Appels", layout="wide")

def get_secret(section: str, key: str) -> str:
    try:
        return st.secrets[section][key]
    except Exception:
        env_key = f"{section.upper()}_{key.upper()}"
        value = os.environ.get(env_key)
        if value is None:
            raise ValueError(f"Secret '{env_key}' introuvable.")
        return value

# --- 1. SECURITE & AUTHENTIFICATION ---
def check_password():
    if "authenticated" not in st.session_state:
        st.session_state.authenticated = False
    if st.session_state.authenticated:
        return True

    col1, col2, col3 = st.columns([1, 2, 1])
    with col2:
        st.title("🔒 Accès Restreint")
        password = st.text_input("Veuillez entrer le mot de passe administrateur", type="password")

        if st.button("Se connecter"):
            if password == get_secret("general", "password"):  # ← CORRIGÉ
                st.session_state.authenticated = True
                st.rerun()
            else:
                st.error("Mot de passe incorrect.")
    return False

if not check_password():
    st.stop()

# --- CONFIGURATION SUPABASE ---
try:
    url = get_secret("supabase", "url")   # ← CORRIGÉ
    key = get_secret("supabase", "key")   # ← CORRIGÉ
    supabase: Client = create_client(url, key)
except Exception as e:
    st.error(f"Erreur de configuration des secrets Supabase : {e}")
    st.stop()

st.title("🎓 SkillQuest - Gestion des Appels")

# --- FONCTIONS UTILITAIRES ---

def get_or_create_student(email, prenom, nom, numero=None):
    # Vérifie si l'étudiant existe par email
    res = supabase.table("students").select("id").eq("email", email).execute()
    if res.data:
        return res.data[0]['id']
    else:
        new_student = {
            "email": email,
            "first_name": prenom,
            "last_name": nom,
            "student_number": str(numero) if numero else None
        }
        res = supabase.table("students").insert(new_student).execute()
        return res.data[0]['id']

# --- INTERFACE ---
tab1, tab2, tab3, tab4 = st.tabs(["📥 Importer Inscriptions", "✅ Faire l'Appel", "📊 Statistiques Globales", "📉 Rapports d'Absence"])


# ==============================================================================
# TAB 1 : IMPORTATION DU FICHIER MOODLE & HISTORIQUE
# ==============================================================================
with tab1:
    col_import, col_hist = st.columns([2, 1])

    with col_hist:
        st.subheader("📜 Historique des imports")
        st.caption("Trié par date et créneau")
        hist_response = supabase.table("sessions").select("date, time_slot, name, import_comment").order("date", desc=True).order("time_slot", desc=True).execute()
        if hist_response.data:
            df_hist = pd.DataFrame(hist_response.data)
            df_hist.columns = ["Date", "Créneau", "Nom", "Note d'import"]
            st.dataframe(df_hist.head(), use_container_width=True, hide_index=True)
        else:
            st.info("Aucune session importée.")

    with col_import:
        st.header("Importer / Mettre à jour une séance")
        
        c1, c2, c3 = st.columns([1.5, 1, 2])
        with c1:
            date_session = st.date_input("Date de la séance", datetime.now())
        with c2:
            creneaux_dispo = ["08h30", "10h15", "13h15", "15h00", "16h45", "18h30"]
            creneau_session = st.selectbox("Créneau", creneaux_dispo)
        with c3:
            nom_session = st.text_input("Nom de la séance", f"Séance du {date_session.strftime('%d/%m')}")

        details_session = st.text_area(
            "Détails de la séance (Aide pour l'appel)",
            height=100,
            placeholder="Activité 01 : ... - G104\nActivité 02 : ... - G110"
        )
        
        import_comment = st.text_input(
            "💬 Commentaire pour les collègues (Optionnel)", 
            placeholder="Ex: Fichier Moodle extrait à 8h15. Il manque 2 étudiants."
        )

        uploaded_file = st.file_uploader("Fichier Excel Moodle (.xlsx)", type=["xlsx"])

        if uploaded_file and st.button("Lancer l'importation", type="primary"):
            with st.spinner("Analyse et filtrage en cours..."):
                try:
                    # 1. Créer ou Mettre à jour la Session
                    session_data = {
                        "date": str(date_session),
                        "time_slot": creneau_session,
                        "name": nom_session,
                        "details": details_session,
                        "import_comment": import_comment
                    }
                    res_session = supabase.table("sessions").upsert(
                        session_data, on_conflict="date, time_slot"
                    ).execute()
                    session_id = res_session.data[0]['id']
                    
                    # 2. Lire Excel
                    df = pd.read_excel(uploaded_file)
                    
                    # 3. Gérer les Activités (On ignore les cases vides pour l'instant)
                    groupes_uniques = df['Groupe'].dropna().unique()
                    existing_acts_resp = supabase.table("activities").select("id, name").eq("session_id", session_id).execute()
                    existing_acts = {a['name']: a['id'] for a in existing_acts_resp.data}
                    activity_mapping = {} 
                    
                    for grp_name in groupes_uniques:
                        grp_str = str(grp_name).strip()
                        if grp_str in existing_acts:
                            activity_mapping[grp_name] = existing_acts[grp_str]
                        else:
                            act_data = {"session_id": session_id, "name": grp_str, "room": "À définir"}
                            res_act = supabase.table("activities").insert(act_data).execute()
                            activity_mapping[grp_name] = res_act.data[0]['id']

                    # --- NOUVEAU : CRÉATION DU GROUPE FANTÔME POUR LES NON-INSCRITS ---
                    nom_groupe_vide = "Sans groupe (Non affecté)"
                    if nom_groupe_vide in existing_acts:
                        ghost_act_id = existing_acts[nom_groupe_vide]
                    else:
                        # On met roll_call_done à True d'office pour qu'ils soient traités comme de vrais absents
                        ghost_data = {"session_id": session_id, "name": nom_groupe_vide, "room": "N/A", "roll_call_done": True}
                        res_ghost = supabase.table("activities").insert(ghost_data).execute()
                        ghost_act_id = res_ghost.data[0]['id']
                    # ------------------------------------------------------------------

                    # 4. Filtrage et Création des Étudiants
                    count_inscrits = 0
                    progress_bar = st.progress(0)
                    total_rows = len(df)
                    
                    for index, row in df.iterrows():
                        # Sécurisation de l'email
                        email = str(row.get('Adresse de courriel', '')).strip().lower()
                        
                        # --- FILTRAGE DES PROFESSEURS ---
                        # Si ce n'est pas un étudiant, on passe à la ligne suivante
                        if not email.endswith("@etu.unilasalle.fr"):
                            progress_bar.progress((index + 1) / total_rows)
                            continue

                        # --- GESTION DES GROUPES VIDES ---
                        is_unassigned = pd.isna(row['Groupe']) or str(row['Groupe']).strip() == ""
                        
                        if is_unassigned:
                            act_id = ghost_act_id  # On l'envoie dans le groupe fantôme
                        else:
                            act_id = activity_mapping[row['Groupe']]

                        # Inscription en base de données
                        prenom = row.get('Prénom', '')
                        nom = row.get('Nom de famille', '')
                        stu_id = get_or_create_student(email, prenom, nom)
                        
                        supabase.table("registrations").upsert(
                            {"student_id": stu_id, "activity_id": act_id, "is_present": False},
                            on_conflict="student_id, activity_id"
                        ).execute()
                        
                        count_inscrits += 1
                        progress_bar.progress((index + 1) / total_rows)
                    
                    st.success(f"✅ Terminé ! {count_inscrits} étudiants traités (les professeurs ont été ignorés).")
                    time.sleep(2)
                    st.rerun()
                    
                except Exception as e:
                    st.error(f"Erreur lors de l'import : {e}")
# ==============================================================================
# TAB 2 : FAIRE L'APPEL (VUE "PILLS" AVEC MÉMOIRE)
# ==============================================================================
with tab2:
    st.header("Feuille de présence numérique")

    # --- NOUVEAU : Fonction pour vider la mémoire des pilules si on change de date ---
    def reset_pills_memory():
        if 'memoire_pills' in st.session_state:
            st.session_state['memoire_pills'] = []
    # --------------------------------------------------------------------------------

    sessions_resp = supabase.table("sessions").select("*").order("date", desc=True).order("time_slot", desc=True).execute()
    sessions = sessions_resp.data
    
    if not sessions:
        st.warning("Aucune session trouvée.")
    else:
        session_options = {s['id']: f"{s['date']} | {s.get('time_slot', 'Heure N/A')} - {s['name']}" for s in sessions}
        
        # On ajoute on_change=reset_pills_memory pour vider la sélection si on change de jour
        selected_session_id = st.selectbox(
            "Choisir la séance", 
            options=list(session_options.keys()), 
            format_func=lambda x: session_options[x],
            on_change=reset_pills_memory 
        )

        current_session = next((s for s in sessions if s['id'] == selected_session_id), None)
        
        if current_session and current_session.get('import_comment'):
            st.warning(f"💬 **Note de l'équipe (Import) :** {current_session['import_comment']}")

        if current_session and current_session.get('details'):
            with st.expander("ℹ️ Voir le détail des activités et salles (Aide-mémoire)", expanded=False):
                st.markdown(current_session['details'].replace("\n", "  \n")) 

        act_resp = supabase.table("activities").select("id, name, roll_call_done").eq("session_id", selected_session_id).order("name").execute()
        activities = act_resp.data
        
        if activities:
            act_options = {a['id']: f"{'✅' if a['roll_call_done'] else '⏳'} {a['name']}" for a in activities}
            
            # --- NOUVEAU : On ajoute key="memoire_pills" pour que Streamlit retienne le choix ---
            selected_act_ids = st.pills(
                "Sélectionnez une ou plusieurs activités pour afficher les étudiants :", 
                options=list(act_options.keys()), 
                format_func=lambda x: act_options[x],
                selection_mode="multi",
                key="memoire_pills" 
            )
            # ------------------------------------------------------------------------------------

            if not selected_act_ids:
                st.info("👆 Cliquez sur les étiquettes ci-dessus pour afficher les listes d'appel.")
            else:
                selected_activities = [a for a in activities if a['id'] in selected_act_ids]
                all_done = all(a['roll_call_done'] for a in selected_activities)

                if all_done:
                    st.success("✅ L'appel a été marqué comme RÉALISÉ pour TOUTES les activités sélectionnées.")
                    if st.button("↩️ Annuler la validation pour la sélection"):
                        supabase.table("activities").update({"roll_call_done": False}).in_("id", selected_act_ids).execute()
                        st.rerun()
                else:
                    st.warning("⚠️ L'appel est en attente pour au moins une des activités sélectionnées.")

                data_resp = supabase.table("registrations")\
                    .select("id, is_present, comment, students(first_name, last_name, email), activities(name)")\
                    .in_("activity_id", selected_act_ids)\
                    .execute()
                
                regs = data_resp.data
                
                if regs:
                    col_info, col_btn = st.columns([2, 1])
                    with col_info:
                        st.info(f"Total inscrits affichés : {len(regs)}")
                    with col_btn:
                        if st.button("✔️ Cocher tous présents", use_container_width=True):
                            with st.spinner("Mise à jour rapide..."):
                                supabase.table("registrations").update({"is_present": True}).in_("activity_id", selected_act_ids).execute()
                            st.rerun()

                    list_for_df = []
                    for r in regs:
                        student = r['students']
                        activity_name = r['activities']['name']
                        list_for_df.append({
                            "reg_id": r['id'],
                            "Activité": activity_name, 
                            "Nom": student['last_name'],
                            "Prénom": student['first_name'],
                            "Email": student['email'],
                            "Présent": r['is_present'],
                            "Commentaire": r['comment'] if r['comment'] else ""
                        })
                    
                    df_appel = pd.DataFrame(list_for_df).sort_values(by=["Activité", "Nom"])
                    
                    edited_df = st.data_editor(
                        df_appel,
                        column_config={
                            "reg_id": None, 
                            "Activité": st.column_config.TextColumn("Activité", disabled=True),
                            "Présent": st.column_config.CheckboxColumn("Présent ?", default=False),
                            "Commentaire": st.column_config.TextColumn("Commentaire", width="large")
                        },
                        disabled=["Activité", "Nom", "Prénom", "Email"],
                        hide_index=True,
                        use_container_width=True
                    )
                    
                    if st.button("💾 Enregistrer et Valider l'appel", type="primary"):
                        with st.spinner("Sauvegarde des présences..."):
                            for index, row in edited_df.iterrows():
                                supabase.table("registrations").update({
                                    "is_present": row['Présent'],
                                    "comment": row['Commentaire'],
                                    "marked_at": datetime.now().isoformat()
                                }).eq("id", row['reg_id']).execute()
                            
                            supabase.table("activities").update({"roll_call_done": True}).in_("id", selected_act_ids).execute()
                                
                        st.success("Modifications enregistrées et appels validés pour la sélection !")
                        time.sleep(1)
                        st.rerun()
                else:
                    st.info("Aucun inscrit dans les groupes sélectionnés.")
        else:
            st.warning("Aucune activité trouvée pour cette session.")
# ==============================================================================
# TAB 3 : STATISTIQUES & ANALYSE DÉTAILLÉE
# ==============================================================================
with tab3:
    st.header("📊 Tableau de Bord Analytique")
    
    if st.button("🔄 Charger / Rafraîchir les données", key="refresh_stats"):
        with st.spinner("Calcul des statistiques en cours..."):
            # 1. Récupération des données INCLUANT le créneau (time_slot) et le statut de l'appel
            response = supabase.table("registrations").select(
                "is_present, activities(name, roll_call_done, sessions(date, time_slot, name))"
            ).execute()
            
            data = response.data
            
            if not data:
                st.warning("Pas assez de données pour générer des statistiques.")
            else:
                # 2. Transformation en DataFrame Pandas
                rows = []
                for item in data:
                    act = item['activities']
                    sess = act['sessions']
                    
                    # NOUVEAU : On exclut les activités où l'appel n'est pas encore fait
                    # pour ne pas fausser les statistiques avec de faux "100% d'absents"
                    if not act.get('roll_call_done', False):
                        continue
                        
                    creneau = sess.get('time_slot', 'N/A')
                    
                    rows.append({
                        "Date": sess['date'],
                        "Créneau": creneau,
                        "Séance Complète": f"{sess['date']} | {creneau}", # Pour le filtre
                        "Session": sess['name'],
                        "Activité": act['name'],
                        "Statut": "Présent" if item['is_present'] else "Absent"
                    })
                
                # STOCKAGE DANS LA SESSION
                st.session_state['df_stats'] = pd.DataFrame(rows)

    # VÉRIFICATION : Est-ce qu'on a des données en mémoire ?
    if 'df_stats' in st.session_state and not st.session_state['df_stats'].empty:
        df_stats = st.session_state['df_stats']

        # --- KPI GLOBAUX ---
        total_inscrits = len(df_stats)
        total_absents = len(df_stats[df_stats["Statut"] == "Absent"])
        taux_absenteisme = (total_absents / total_inscrits) * 100 if total_inscrits > 0 else 0

        kpi1, kpi2, kpi3 = st.columns(3)
        kpi1.metric("Total Inscriptions Évaluées", total_inscrits, help="Ne compte que les appels validés.")
        kpi2.metric("Total Absences", total_absents)
        kpi3.metric("Taux d'absentéisme Global", f"{taux_absenteisme:.1f} %", delta_color="inverse")

        st.divider()

        # --- ANALYSE PAR SÉANCE (DATE + CRÉNEAU) ---
        st.subheader("📅 Absentéisme par Séance")
        
        # On groupe maintenant par Date ET Créneau
        df_session = df_stats.groupby(["Date", "Créneau", "Session"]).agg(
            Inscrits=('Statut', 'count'),
            Absents=('Statut', lambda x: (x == 'Absent').sum())
        ).reset_index()
        
        # Calcul du % (entre 0 et 1 pour que le formatage Streamlit fonctionne parfaitement)
        df_session["% Absentéisme"] = 100*(df_session["Absents"] / df_session["Inscrits"])
        
        # Tri chronologique inverse
        df_session = df_session.sort_values(by=["Date", "Créneau"], ascending=[False, False])
        
        st.dataframe(
            df_session,
            column_config={
                "% Absentéisme": st.column_config.ProgressColumn(
                    "Taux d'absence",
                    format="%.1f%%", # Affiche en %
                    min_value=0,
                    max_value=100, # Basé sur un ratio de 0 à 1
                ),
            },
            use_container_width=True,
            hide_index=True
        )

        st.divider()

        # --- ANALYSE DÉTAILLÉE PAR ACTIVITÉ ---
        st.subheader("🏊 Détail par Activité")
        st.caption("Filtrer pour voir les détails d'une séance spécifique.")

        # FILTRE INTERACTIF PAR SÉANCE (Date + Créneau)
        seances_dispo = sorted(df_stats["Séance Complète"].unique(), reverse=True)
        selected_filter = st.selectbox("Filtrer par séance :", ["Toutes les séances"] + list(seances_dispo))

        # Application du filtre
        if selected_filter == "Toutes les séances":
            df_filtered = df_stats
        else:
            df_filtered = df_stats[df_stats["Séance Complète"] == selected_filter]

        # Groupement par Activité en incluant le Créneau
        df_activity = df_filtered.groupby(["Activité", "Créneau", "Session"]).agg(
            Inscrits=('Statut', 'count'),
            Absents=('Statut', lambda x: (x == 'Absent').sum())
        ).reset_index()

        if not df_activity.empty:
            df_activity["% Absentéisme"] = 100*(df_activity["Absents"] / df_activity["Inscrits"])
            
            # Tri par taux d'absentéisme décroissant (les pires en haut)
            df_activity = df_activity.sort_values(by="% Absentéisme", ascending=False)

            # On réorganise les colonnes pour un affichage plus propre
            df_activity = df_activity[["Créneau", "Activité", "Session", "Inscrits", "Absents", "% Absentéisme"]]

            st.dataframe(
                df_activity,
                column_config={
                    "% Absentéisme": st.column_config.ProgressColumn(
                        "Taux d'absence",
                        format="%.1f%%",
                        min_value=0,
                        max_value=100,
                    ),
                },
                use_container_width=True,
                hide_index=True
            )
        else:
            st.info("Aucune donnée pour cette sélection.")
    
    elif 'df_stats' in st.session_state and st.session_state['df_stats'].empty:
        st.info("Aucune statistique disponible. Assurez-vous d'avoir validé au moins un appel (bouton '✅ Enregistrer et Valider l'appel' dans l'onglet Appel).")
    else:
        st.info("Cliquez sur le bouton ci-dessus pour charger les statistiques.")


# ==============================================================================
# TAB 4 : RAPPORTS D'ABSENCE (VUE SCINDÉE)
# ==============================================================================
with tab4:
    st.header("📉 Rapport d'Absence par Séance")
    
    if not sessions:
        st.warning("Aucune session disponible.")
    else:
        session_options_rpt = {s['id']: f"{s['date']} | {s.get('time_slot', 'Heure N/A')} - {s['name']}" for s in sessions}
        rpt_session_id = st.selectbox("Sélectionnez la séance du rapport :", options=list(session_options_rpt.keys()), format_func=lambda x: session_options_rpt[x])

        if st.button("Générer les listes"):
            with st.spinner("Analyse des données..."):
                acts_resp = supabase.table("activities").select("id, name, roll_call_done").eq("session_id", rpt_session_id).execute()
                act_ids = [a['id'] for a in acts_resp.data]
                
                if not act_ids:
                    st.warning("Aucune activité trouvée pour cette session.")
                else:
                    # On récupère toutes les inscriptions où l'étudiant n'est PAS coché présent
                    absents_resp = supabase.table("registrations")\
                        .select("is_present, comment, students(first_name, last_name, email), activities(name, roll_call_done)")\
                        .in_("activity_id", act_ids)\
                        .eq("is_present", False)\
                        .execute()
                    
                    data_absents = absents_resp.data
                    
                    if data_absents:
                        list_confirmes = []
                        list_en_attente = []
                        
                        for item in data_absents:
                            stu = item['students']
                            act = item['activities']
                            
                            row_data = {
                                "Activité": act['name'],
                                "Nom": stu['last_name'],
                                "Prénom": stu['first_name'],
                                "Email": stu['email'],
                                "Commentaire": item['comment'] if item['comment'] else ""
                            }
                            
                            # Tri selon que l'appel a été fait ou non dans cette activité
                            if act['roll_call_done']:
                                list_confirmes.append(row_data)
                            else:
                                list_en_attente.append(row_data)
                        
                        # --- VUE 1 : VRAIS ABSENTS ---
                        st.subheader("🔴 Absents Confirmés")
                        st.caption("Étudiants marqués absents dans les activités où l'appel a été validé.")
                        if list_confirmes:
                            df_confirmes = pd.DataFrame(list_confirmes).sort_values(by=["Activité", "Nom"])
                            st.dataframe(df_confirmes, use_container_width=True, hide_index=True)
                            
                            csv_conf = df_confirmes.to_csv(index=False).encode('utf-8')
                            st.download_button("📥 Télécharger Absents Confirmés (CSV)", data=csv_conf, file_name=f"Absents_Confirmes_{session_options_rpt[rpt_session_id]}.csv", mime='text/csv')
                        else:
                            st.success("Aucun absent confirmé ! 🎉")

                        st.divider()

                        # --- VUE 2 : EN ATTENTE D'APPEL ---
                        st.subheader("⏳ Appel Non Réalisé (Statut Inconnu)")
                        st.caption("Étudiants inscrits dans des groupes où l'enseignant n'a pas encore validé l'appel.")
                        if list_en_attente:
                            df_attente = pd.DataFrame(list_en_attente).sort_values(by=["Activité", "Nom"])
                            st.dataframe(df_attente, use_container_width=True, hide_index=True)
                            
                            csv_att = df_attente.to_csv(index=False).encode('utf-8')
                            st.download_button("📥 Télécharger En Attente (CSV)", data=csv_att, file_name=f"En_Attente_{session_options_rpt[rpt_session_id]}.csv", mime='text/csv')
                        else:
                            st.info("Tous les appels ont été réalisés pour cette séance.")
                            
                    else:
                        st.success("Aucun étudiant absent détecté (100% de présence validée) !")
