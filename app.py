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

    # --- Historique (Affichage du commentaire) ---
    with col_hist:
        st.subheader("📜 Historique des imports")
        st.caption("Trié par date et créneau")
        # On récupère import_comment en plus
        hist_response = supabase.table("sessions").select("date, time_slot, name, import_comment").order("date", desc=True).order("time_slot", desc=True).execute()
        if hist_response.data:
            df_hist = pd.DataFrame(hist_response.data)
            df_hist.columns = ["Date", "Créneau", "Nom", "Note d'import"]
            st.dataframe(df_hist, use_container_width=True, hide_index=True)
        else:
            st.info("Aucune session importée.")

    # --- Importation ---
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
        
        # NOUVEAU : Le commentaire pour les collègues
        import_comment = st.text_input(
            "💬 Commentaire pour les collègues (Optionnel)", 
            placeholder="Ex: Fichier Moodle extrait à 8h15. Il manque 2 étudiants, je les rajouterai ce midi."
        )

        uploaded_file = st.file_uploader("Fichier Excel Moodle (.xlsx)", type=["xlsx"])

        if uploaded_file and st.button("Lancer l'importation", type="primary"):
            with st.spinner("Analyse et mise à jour en cours..."):
                try:
                    # 1. Créer ou Mettre à jour la Session (UPSERT)
                    session_data = {
                        "date": str(date_session),
                        "time_slot": creneau_session,
                        "name": nom_session,
                        "details": details_session,
                        "import_comment": import_comment # On sauvegarde la note
                    }
                    res_session = supabase.table("sessions").upsert(
                        session_data, on_conflict="date, time_slot"
                    ).execute()
                    
                    session_id = res_session.data[0]['id']
                    
                    # 2. Lire Excel
                    df = pd.read_excel(uploaded_file)
                    
                    # 3. Gérer les Activités
                    groupes_uniques = df['Groupe'].unique()
                    existing_acts_resp = supabase.table("activities").select("id, name").eq("session_id", session_id).execute()
                    existing_acts = {a['name']: a['id'] for a in existing_acts_resp.data}
                    activity_mapping = {} 
                    
                    for grp_name in groupes_uniques:
                        if pd.isna(grp_name): continue
                        grp_str = str(grp_name).strip()
                        
                        if grp_str in existing_acts:
                            activity_mapping[grp_name] = existing_acts[grp_str]
                        else:
                            act_data = {"session_id": session_id, "name": grp_str, "room": "À définir"}
                            res_act = supabase.table("activities").insert(act_data).execute()
                            activity_mapping[grp_name] = res_act.data[0]['id']
                    
                    # 4. Gérer les Étudiants
                    count_inscrits = 0
                    progress_bar = st.progress(0)
                    total_rows = len(df)
                    
                    for index, row in df.iterrows():
                        if pd.isna(row['Groupe']): continue
                        
                        email = row.get('Adresse de courriel', '')
                        prenom = row.get('Prénom', '')
                        nom = row.get('Nom de famille', '')
                        
                        stu_id = get_or_create_student(email, prenom, nom)
                        
                        if row['Groupe'] in activity_mapping:
                            act_id = activity_mapping[row['Groupe']]
                            supabase.table("registrations").upsert(
                                {"student_id": stu_id, "activity_id": act_id, "is_present": False},
                                on_conflict="student_id, activity_id"
                            ).execute()
                            count_inscrits += 1
                        
                        progress_bar.progress((index + 1) / total_rows)
                    
                    st.success(f"✅ Terminé ! {count_inscrits} inscriptions synchronisées.")
                    time.sleep(1)
                    st.rerun()
                    
                except Exception as e:
                    st.error(f"Erreur lors de l'import : {e}")
# ==============================================================================
# TAB 2 : FAIRE L'APPEL
# ==============================================================================
with tab2:
    st.header("Feuille de présence numérique")

    sessions_resp = supabase.table("sessions").select("*").order("date", desc=True).order("time_slot", desc=True).execute()
    sessions = sessions_resp.data
    
    if not sessions:
        st.warning("Aucune session trouvée.")
    else:
        session_options = {s['id']: f"{s['date']} | {s.get('time_slot', 'Heure N/A')} - {s['name']}" for s in sessions}
        selected_session_id = st.selectbox("Choisir la séance", options=list(session_options.keys()), format_func=lambda x: session_options[x])

        current_session = next((s for s in sessions if s['id'] == selected_session_id), None)
        
        # NOUVEAU : Affichage bien visible du commentaire d'import s'il y en a un
        if current_session and current_session.get('import_comment'):
            st.warning(f"💬 **Note de l'équipe (Import) :** {current_session['import_comment']}")

        if current_session and current_session.get('details'):
            with st.expander("ℹ️ Voir le détail des activités et salles (Aide-mémoire)", expanded=True):
                st.markdown(current_session['details'].replace("\n", "  \n")) 

        act_resp = supabase.table("activities").select("*").eq("session_id", selected_session_id).order("name").execute()
        activities = act_resp.data
        
        if activities:
            act_options = {a['id']: a['name'] for a in activities}
            selected_act_id = st.selectbox("Choisir l'activité", options=list(act_options.keys()), format_func=lambda x: act_options[x])
            
            data_resp = supabase.table("registrations")\
                .select("id, is_present, comment, students(first_name, last_name, email)")\
                .eq("activity_id", selected_act_id)\
                .execute()
            
            regs = data_resp.data
            
            if regs:
                list_for_df = []
                for r in regs:
                    student = r['students']
                    list_for_df.append({
                        "reg_id": r['id'],
                        "Nom": student['last_name'],
                        "Prénom": student['first_name'],
                        "Email": student['email'],
                        "Présent": r['is_present'],
                        "Commentaire": r['comment'] if r['comment'] else ""
                    })
                
                df_appel = pd.DataFrame(list_for_df).sort_values("Nom")
                
                st.info(f"Inscrits : {len(df_appel)}")
                
                edited_df = st.data_editor(
                    df_appel,
                    column_config={
                        "reg_id": None, 
                        "Présent": st.column_config.CheckboxColumn("Présent ?", default=False),
                        "Commentaire": st.column_config.TextColumn("Commentaire", width="large")
                    },
                    disabled=["Nom", "Prénom", "Email"],
                    hide_index=True,
                    use_container_width=True
                )
                
                if st.button("💾 Enregistrer l'appel", type="primary"):
                    with st.spinner("Sauvegarde..."):
                        for index, row in edited_df.iterrows():
                            supabase.table("registrations").update({
                                "is_present": row['Présent'],
                                "comment": row['Commentaire'],
                                "marked_at": datetime.now().isoformat()
                            }).eq("id", row['reg_id']).execute()
                        
                    st.success("Appel enregistré !")
            else:
                st.info("Aucun inscrit dans ce groupe.")
        else:
            st.warning("Aucune activité trouvée pour cette session.")

# ==============================================================================
# TAB 3 : STATISTIQUES & ANALYSE DÉTAILLÉE (CORRIGÉ)
# ==============================================================================
with tab3:
    st.header("📊 Tableau de Bord Analytique")
    
    # Bouton pour charger/rafraîchir les données
    if st.button("🔄 Charger / Rafraîchir les données", key="refresh_stats"):
        with st.spinner("Calcul des statistiques en cours..."):
            # 1. Récupération de TOUTES les données
            response = supabase.table("registrations").select(
                "is_present, activities(name, sessions(date, name))"
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
                    rows.append({
                        "Date": sess['date'],
                        "Session": sess['name'],
                        "Activité": act['name'],
                        "Statut": "Présent" if item['is_present'] else "Absent"
                    })
                
                # STOCKAGE DANS LA SESSION (C'est ici que la magie opère)
                st.session_state['df_stats'] = pd.DataFrame(rows)

    # VÉRIFICATION : Est-ce qu'on a des données en mémoire ?
    if 'df_stats' in st.session_state:
        df_stats = st.session_state['df_stats']

        # --- KPI GLOBAUX ---
        total_inscrits = len(df_stats)
        total_absents = len(df_stats[df_stats["Statut"] == "Absent"])
        taux_absenteisme = (total_absents / total_inscrits) * 100 if total_inscrits > 0 else 0

        kpi1, kpi2, kpi3 = st.columns(3)
        kpi1.metric("Total Inscriptions", total_inscrits)
        kpi2.metric("Total Absences", total_absents)
        kpi3.metric("Taux d'absentéisme Global", f"{taux_absenteisme:.1f} %", delta_color="inverse")

        st.divider()

        # --- ANALYSE PAR SESSION (DATE) ---
        st.subheader("📅 Absentéisme par Session")
        
        df_session = df_stats.groupby(["Date", "Session"]).agg(
            Inscrits=('Statut', 'count'),
            Absents=('Statut', lambda x: (x == 'Absent').sum())
        ).reset_index()
        
        df_session["% Absentéisme"] = (100*df_session["Absents"] / df_session["Inscrits"])
        
        st.dataframe(
            df_session.style.format({"% Absentéisme": "{:.1%}"}),
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

        st.divider()

        # --- ANALYSE DÉTAILLÉE PAR ACTIVITÉ ---
        st.subheader("🏊 Détail par Activité")
        st.caption("Filtrer pour voir les détails d'une date spécifique.")

        # FILTRE INTERACTIF (Ne fait plus disparaître les données)
        dates_dispo = sorted(df_stats["Date"].unique(), reverse=True)
        selected_date_filter = st.selectbox("Filtrer par date", ["Toutes les dates"] + list(dates_dispo))

        # Application du filtre
        if selected_date_filter == "Toutes les dates":
            df_filtered = df_stats
        else:
            df_filtered = df_stats[df_stats["Date"] == selected_date_filter]

        # Groupement par Activité
        df_activity = df_filtered.groupby(["Activité", "Session"]).agg(
            Inscrits=('Statut', 'count'),
            Absents=('Statut', lambda x: (x == 'Absent').sum())
        ).reset_index()

        if not df_activity.empty:
            df_activity["% Absentéisme"] = (100*df_activity["Absents"] / df_activity["Inscrits"])
            
            # Tri par taux d'absentéisme décroissant
            df_activity = df_activity.sort_values(by="% Absentéisme", ascending=False)

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
    
    else:
        st.info("Cliquez sur le bouton ci-dessus pour charger les statistiques.")


# ==============================================================================
# TAB 4 : RAPPORTS D'ABSENCE (AVEC COMMENTAIRES)
# ==============================================================================
# ==============================================================================
# TAB 4 : RAPPORTS D'ABSENCE
# ==============================================================================
with tab4:
    st.header("📉 Rapport d'Absence par Séance")
    st.caption("Liste filtrée des étudiants n'ayant pas été marqués présents.")

    if not sessions:
        st.warning("Aucune session disponible.")
    else:
        # Mise à jour de l'affichage avec le créneau
        session_options_rpt = {s['id']: f"{s['date']} | {s.get('time_slot', 'Heure N/A')} - {s['name']}" for s in sessions}
        rpt_session_id = st.selectbox("Sélectionnez la séance du rapport :", options=list(session_options_rpt.keys()), format_func=lambda x: session_options_rpt[x])

        if st.button("Générer la liste des absents"):
            with st.spinner("Récupération des données..."):
                acts_resp = supabase.table("activities").select("id, name").eq("session_id", rpt_session_id).execute()
                act_ids = [a['id'] for a in acts_resp.data]
                
                if not act_ids:
                    st.warning("Aucune activité trouvée pour cette session.")
                else:
                    # Ajout de 'comment' dans la requête
                    absents_resp = supabase.table("registrations")\
                        .select("is_present, comment, students(first_name, last_name, email), activities(name)")\
                        .in_("activity_id", act_ids)\
                        .eq("is_present", False)\
                        .execute()
                    
                    data_absents = absents_resp.data
                    
                    if data_absents:
                        clean_list = []
                        for item in data_absents:
                            stu = item['students']
                            act = item['activities']
                            clean_list.append({
                                "Activité": act['name'],
                                "Nom": stu['last_name'],
                                "Prénom": stu['first_name'],
                                "Email": stu['email'],
                                "Commentaire": item['comment'] if item['comment'] else "" # Afficher le motif
                            })
                        
                        df_absents = pd.DataFrame(clean_list).sort_values(by=["Activité", "Nom"])
                        
                        st.subheader(f"Absents : {len(df_absents)} étudiants")
                        st.dataframe(df_absents, use_container_width=True, hide_index=True)
                        
                        csv = df_absents.to_csv(index=False).encode('utf-8')
                        filename = f"Absents_{session_options_rpt[rpt_session_id]}.csv"
                        
                        st.download_button(
                            label="📥 Télécharger la liste (CSV)",
                            data=csv,
                            file_name=filename,
                            mime='text/csv',
                        )
                    else:
                        st.success("Aucun absent détecté pour cette session.")
