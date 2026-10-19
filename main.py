import os
import sqlite3
import shutil
import csv
import json
import hashlib
import secrets
from datetime import datetime, date

from kivy.app import App
from kivy.core.window import Window
from kivy.metrics import dp
from kivy.clock import Clock
from kivy.uix.boxlayout import BoxLayout
from kivy.uix.gridlayout import GridLayout
from kivy.uix.label import Label
from kivy.uix.button import Button
from kivy.uix.popup import Popup
from kivy.uix.scrollview import ScrollView
from kivy.uix.textinput import TextInput
from kivy.uix.spinner import Spinner

DB = "/storage/emulated/0/boutiquefacile.db"
BACKUP_DIR = "/storage/emulated/0/Download"
SECURITY_FILE = "/storage/emulated/0/boutiquefacile_securite.json"
AUTO_BACKUP_INTERVAL = 6 * 60 * 60  # 6 heures, tant que l’application reste ouverte
MAX_AUTO_BACKUPS = 10


def date_maintenant():
    return datetime.now().strftime("%Y-%m-%d %H:%M:%S")


def connecter():
    if not os.path.isfile(DB):
        raise FileNotFoundError(
            f"Base de données introuvable : {DB}\n"
            "Vérifie le chemin avant de continuer."
        )
    conn = sqlite3.connect(DB, timeout=15)
    conn.execute("PRAGMA foreign_keys=ON")
    return conn


def verifier_structure():
    structures = {
        "produits": {"id", "nom", "categorie", "prix_achat", "prix_vente",
                     "stock_initial", "stock_minimum"},
        "clients": {"id", "nom", "telephone", "date_creation"},
        "ventes": {"id", "produit_id", "date_vente", "quantite",
                   "prix_unitaire", "cout_unitaire", "type_vente"},
        "entrees_stock": {"id", "produit_id", "date_entree", "quantite",
                          "cout_unitaire"},
        "dettes": {"id", "client_id", "produit_id", "date_dette",
                   "quantite", "prix_unitaire", "cout_unitaire", "montant_total"},
        "remboursements": {"id", "dette_id", "date_remboursement", "montant"},
        "depenses": {"id", "date_depense", "categorie", "description", "montant"},
    }
    with connecter() as conn:
        tables = {r[0] for r in conn.execute(
            "SELECT name FROM sqlite_master WHERE type='table'")}
        for table, attendues in structures.items():
            if table not in tables:
                raise RuntimeError(
                    f"La table « {table} » manque dans la base existante. "
                    "Aucune table n'a été recréée."
                )
            presentes = {r[1] for r in conn.execute(
                f'PRAGMA table_info("{table}")')}
            manquantes = attendues - presentes
            if manquantes:
                raise RuntimeError(
                    f"Colonnes manquantes dans {table} : "
                    + ", ".join(sorted(manquantes))
                )


def get_stock_reel(pid, conn=None):
    fermer = conn is None
    if fermer:
        conn = connecter()
    try:
        row = conn.execute("""
            SELECT p.stock_initial
              + COALESCE((SELECT SUM(e.quantite) FROM entrees_stock e
                          WHERE e.produit_id=p.id), 0)
              - COALESCE((SELECT SUM(v.quantite) FROM ventes v
                          WHERE v.produit_id=p.id), 0)
            FROM produits p WHERE p.id=?
        """, (pid,)).fetchone()
        return row[0] if row else 0
    finally:
        if fermer:
            conn.close()


def entier(texte, nom, minimum=0):
    try:
        n = int(str(texte).strip())
    except (ValueError, TypeError):
        raise ValueError(f"{nom} doit être un nombre entier.")
    if n < minimum:
        raise ValueError(f"{nom} doit être au moins {minimum}.")
    return n


def nombre(texte, nom, minimum=0):
    try:
        n = float(str(texte).strip().replace(",", "."))
    except (ValueError, TypeError):
        raise ValueError(f"{nom} doit être un nombre valide.")
    if n < minimum:
        raise ValueError(f"{nom} doit être au moins {minimum}.")
    return n


class BoutiqueFacileApp(App):
    def __init__(self, **kwargs):
        super().__init__(**kwargs)
        self.dashboard_labels = {}
        self._dashboard_clock = None
        self._backup_clock = None
        self._pin_popup = None

    def afficher_message(self, titre, message):
        # Le contenu reste dans la zone visible et peut défiler verticalement.
        box = BoxLayout(orientation="vertical", padding=dp(10), spacing=dp(8))
        with box.canvas.before:
            from kivy.graphics import Color, RoundedRectangle
            Color(0.055, 0.075, 0.13, 1)
            _popup_bg = RoundedRectangle(pos=box.pos, size=box.size, radius=[dp(16)])
        box.bind(pos=lambda obj, val: setattr(_popup_bg, "pos", val), size=lambda obj, val: setattr(_popup_bg, "size", val))
        scroll = ScrollView(
            do_scroll_x=False, do_scroll_y=True,
            scroll_type=["bars", "content"], bar_width=dp(5),
        )
        texte = TextInput(
            text=str(message), readonly=True, multiline=True,
            font_size="16sp", foreground_color=(0.92, 0.96, 1, 1), background_color=(0.09, 0.12, 0.19, 1), cursor_color=(0.1, 0.9, 0.55, 1), size_hint=(1, None),
            height=max(dp(220), dp(30) * (str(message).count("\n") + 3)),
            padding=[dp(8), dp(8), dp(8), dp(8)],
        )
        scroll.add_widget(texte)
        box.add_widget(scroll)
        fermer = Button(text="Fermer", size_hint_y=None, height=dp(48), background_normal="", background_color=(0.05, 0.72, 0.43, 1), color=(1, 1, 1, 1), bold=True)
        box.add_widget(fermer)
        pop = Popup(title=titre, content=box, size_hint=(0.94, 0.84), auto_dismiss=True,
                    background="", background_color=(0.035, 0.055, 0.10, 1),
                    title_color=(0.30, 0.95, 0.68, 1), title_size="18sp")
        fermer.bind(on_release=pop.dismiss)
        pop.open()

    def erreur(self, titre, exc):
        self.afficher_message(titre, str(exc))

    def formulaire(self, titre, champs, action, hauteur=0.82):
        # Formulaire défilant : bouton Enregistrer toujours séparé de la liste
        # des champs, et champ actif amené dans la zone visible au clavier.
        box = BoxLayout(orientation="vertical", padding=dp(10), spacing=dp(8))
        scroll = ScrollView(
            do_scroll_x=False, do_scroll_y=True,
            scroll_type=["bars", "content"], bar_width=dp(5),
        )
        contenu = BoxLayout(
            orientation="vertical", size_hint_y=None,
            size_hint_x=1, spacing=dp(5), padding=[0, dp(4), 0, dp(8)],
        )
        contenu.bind(minimum_height=contenu.setter("height"))
        champs_ui = {}
        for c in champs:
            etiquette = Label(
                text=c["label"], size_hint_y=None, height=dp(32),
                halign="left", valign="middle", font_size="15sp", color=(0.88, 0.93, 1, 1),
            )
            etiquette.bind(size=lambda obj, value: setattr(obj, "text_size", value))
            contenu.add_widget(etiquette)
            ti = TextInput(
                text=str(c.get("value", "")), hint_text=c.get("hint", ""),
                multiline=False, size_hint=(1, None), height=dp(48),
                font_size="16sp", padding=[dp(8), dp(10), dp(8), dp(8)], foreground_color=(0.95, 0.97, 1, 1), background_color=(0.10, 0.13, 0.21, 1), cursor_color=(0.1, 0.9, 0.55, 1), hint_text_color=(0.55, 0.62, 0.72, 1),
            )
            if c.get("filtre") == "int":
                ti.input_filter = "int"
            champs_ui[c["key"]] = ti
            contenu.add_widget(ti)

            def suivre_focus(widget, actif, scroller=scroll):
                if actif:
                    # Laisse Android afficher le clavier avant de déplacer le champ.
                    Clock.schedule_once(lambda _dt: scroller.scroll_to(
                        widget, padding=dp(18), animate=True), 0.25)

            ti.bind(focus=suivre_focus)

        scroll.add_widget(contenu)
        box.add_widget(scroll)
        bouton = Button(text="Enregistrer", size_hint_y=None, height=dp(50), background_normal="", background_color=(0.05, 0.72, 0.43, 1), color=(1, 1, 1, 1), bold=True)
        box.add_widget(bouton)
        pop = Popup(
            title=titre, content=box, size_hint=(0.94, hauteur),
            auto_dismiss=True, background="",
            background_color=(0.035, 0.055, 0.10, 1),
            title_color=(0.30, 0.95, 0.68, 1), title_size="18sp",
        )

        def valider(_):
            try:
                action(champs_ui, pop)
            except Exception as exc:
                self.erreur("Erreur", exc)

        bouton.bind(on_release=valider)
        pop.open()

    def choisir(self, titre, options, action):
        if not options:
            self.afficher_message(titre, "Aucun élément disponible.")
            return
        box = BoxLayout(orientation="vertical", padding=dp(8), spacing=dp(6))
        scroll = ScrollView(
            do_scroll_x=False, do_scroll_y=True,
            scroll_type=["bars", "content"], bar_width=dp(5),
        )
        liste = BoxLayout(orientation="vertical", size_hint_y=None, spacing=dp(5))
        liste.bind(minimum_height=liste.setter("height"))
        pop = Popup(title=titre, content=box, size_hint=(0.94, 0.8),
                    background="", background_color=(0.035, 0.055, 0.10, 1),
                    title_color=(0.30, 0.95, 0.68, 1), title_size="18sp")
        scroll.add_widget(liste)
        box.add_widget(scroll)
        for ident, nom in options:
            b = Button(text=str(nom), size_hint_y=None, height=dp(50), font_size="15sp", background_normal="", background_color=(0.10, 0.14, 0.23, 1), color=(0.92, 0.96, 1, 1))

            def choisir_element(_, ident=ident):
                pop.dismiss()
                action(ident)

            b.bind(on_release=choisir_element)
            liste.add_widget(b)
        pop.open()

    # Produits
    def ajouter_produit_gui(self, *_):
        champs = [
            {"key": "nom", "label": "Nom du produit"},
            {"key": "cat", "label": "Catégorie"},
            {"key": "achat", "label": "Prix d'achat", "value": "0"},
            {"key": "vente", "label": "Prix de vente", "value": "0"},
            {"key": "stock", "label": "Stock initial", "value": "0"},
            {"key": "mini", "label": "Stock minimum", "value": "5"},
        ]

        def enregistrer(e, pop):
            nom = e["nom"].text.strip()
            if not nom:
                raise ValueError("Le nom du produit est obligatoire.")
            achat = nombre(e["achat"].text, "Prix d'achat")
            vente = nombre(e["vente"].text, "Prix de vente")
            stock = entier(e["stock"].text, "Stock initial")
            mini = entier(e["mini"].text, "Stock minimum")
            with connecter() as conn:
                conn.execute("""
                    INSERT INTO produits
                    (nom, categorie, prix_achat, prix_vente, stock_initial, stock_minimum)
                    VALUES (?, ?, ?, ?, ?, ?)
                """, (nom, e["cat"].text.strip(), achat, vente, stock, mini))
            pop.dismiss()
            self.afficher_message("Succès", f"Produit ajouté : {nom}")

        self.formulaire("Ajouter un produit", champs, enregistrer)

    def produits_stock_gui(self, *_):
        with connecter() as conn:
            produits = conn.execute("""
                SELECT id, nom, categorie, prix_achat, prix_vente, stock_minimum
                FROM produits ORDER BY nom
            """).fetchall()
        lignes = []
        for pid, nom, cat, achat, vente, mini in produits:
            stock = get_stock_reel(pid)
            etat = "⚠ STOCK FAIBLE" if stock <= mini else "Stock suffisant"
            lignes.append(
                f"{nom}\nCatégorie : {cat or 'Non précisée'}\n"
                f"Prix d'achat : {achat} FCFA\nPrix de vente : {vente} FCFA\n"
                f"Stock disponible : {stock}\nStock minimum : {mini}\n{etat}\n"
                + "-" * 25
            )
        self.afficher_message("Produits et stock",
                              "\n".join(lignes) if lignes else "Aucun produit.")

    def gerer_produits_gui(self, *_):
        with connecter() as conn:
            produits = conn.execute(
                "SELECT id, nom FROM produits ORDER BY nom").fetchall()
        self.choisir(
            "Choisir un produit",
            [(pid, f"{nom} (stock : {get_stock_reel(pid)})")
             for pid, nom in produits],
            self.menu_produit
        )

    def menu_produit(self, pid):
        with connecter() as conn:
            p = conn.execute("""
                SELECT nom, categorie, prix_achat, prix_vente,
                       stock_initial, stock_minimum
                FROM produits WHERE id=?
            """, (pid,)).fetchone()
        if not p:
            self.afficher_message("Produit", "Produit introuvable.")
            return
        nom, cat, achat, vente, stock, mini = p
        box = BoxLayout(orientation="vertical", padding=10, spacing=8)
        box.add_widget(Label(text=(
            f"{nom}\nCatégorie : {cat or 'Non précisée'}\n"
            f"Achat : {achat} FCFA\nVente : {vente} FCFA\n"
            f"Stock : {get_stock_reel(pid)}\nMinimum : {mini}"
        )))
        pop = Popup(title="Gestion du produit", content=box, size_hint=(0.9, 0.65))
        for titre, fonction in [
            ("Modifier", lambda *_: (pop.dismiss(), self.modifier_produit_gui(pid))),
            ("Supprimer", lambda *_: (pop.dismiss(), self.supprimer_produit_gui(pid, nom))),
            ("Fermer", lambda *_: pop.dismiss()),
        ]:
            b = Button(text=titre, size_hint_y=None, height=46)
            b.bind(on_release=fonction)
            box.add_widget(b)
        pop.open()

    def modifier_produit_gui(self, pid):
        with connecter() as conn:
            p = conn.execute("""
                SELECT nom, categorie, prix_achat, prix_vente, stock_initial, stock_minimum
                FROM produits WHERE id=?
            """, (pid,)).fetchone()
        if not p:
            return
        nom, cat, achat, vente, stock, mini = p
        champs = [
            {"key": "nom", "label": "Nom", "value": nom},
            {"key": "cat", "label": "Catégorie", "value": cat or ""},
            {"key": "achat", "label": "Prix d'achat", "value": achat},
            {"key": "vente", "label": "Prix de vente", "value": vente},
            {"key": "stock", "label": "Stock initial", "value": stock},
            {"key": "mini", "label": "Stock minimum", "value": mini},
        ]

        def enregistrer(e, pop):
            nom2 = e["nom"].text.strip()
            if not nom2:
                raise ValueError("Le nom est obligatoire.")
            achat2 = nombre(e["achat"].text, "Prix d'achat")
            vente2 = nombre(e["vente"].text, "Prix de vente")
            stock2 = entier(e["stock"].text, "Stock initial")
            mini2 = entier(e["mini"].text, "Stock minimum")
            with connecter() as conn:
                entrees, sorties = conn.execute("""
                    SELECT
                      COALESCE((SELECT SUM(quantite) FROM entrees_stock WHERE produit_id=?),0),
                      COALESCE((SELECT SUM(quantite) FROM ventes WHERE produit_id=?),0)
                """, (pid, pid)).fetchone()
                if stock2 + entrees - sorties < 0:
                    raise ValueError("Ce stock initial rendrait le stock négatif.")
                conn.execute("""
                    UPDATE produits SET nom=?, categorie=?, prix_achat=?, prix_vente=?,
                    stock_initial=?, stock_minimum=? WHERE id=?
                """, (nom2, e["cat"].text.strip(), achat2, vente2, stock2, mini2, pid))
            pop.dismiss()
            self.afficher_message("Succès", "Produit modifié.")

        self.formulaire("Modifier un produit", champs, enregistrer)

    def supprimer_produit_gui(self, pid, nom):
        box = BoxLayout(orientation="vertical", padding=10, spacing=10)
        box.add_widget(Label(text=f"Supprimer {nom} ?"))
        boutons = BoxLayout(size_hint_y=None, height=48, spacing=8)
        oui, non = Button(text="Supprimer"), Button(text="Annuler")
        boutons.add_widget(oui)
        boutons.add_widget(non)
        box.add_widget(boutons)
        pop = Popup(title="Confirmation", content=box, size_hint=(0.9, 0.4))

        def confirmer(_):
            try:
                with connecter() as conn:
                    lie = conn.execute("""
                        SELECT
                          EXISTS(SELECT 1 FROM ventes WHERE produit_id=?)
                          OR EXISTS(SELECT 1 FROM entrees_stock WHERE produit_id=?)
                          OR EXISTS(SELECT 1 FROM dettes WHERE produit_id=?)
                    """, (pid, pid, pid)).fetchone()[0]
                    if lie:
                        raise ValueError(
                            "Suppression impossible : ce produit possède un historique. "
                            "Cela protège les comptes."
                        )
                    conn.execute("DELETE FROM produits WHERE id=?", (pid,))
                pop.dismiss()
                self.afficher_message("Succès", "Produit supprimé.")
            except Exception as exc:
                self.erreur("Suppression impossible", exc)

        oui.bind(on_release=confirmer)
        non.bind(on_release=pop.dismiss)
        pop.open()

    # Clients
    def ajouter_client_gui(self, *_):
        champs = [
            {"key": "nom", "label": "Nom du client"},
            {"key": "tel", "label": "Téléphone"},
        ]

        def enregistrer(e, pop):
            nom = e["nom"].text.strip()
            if not nom:
                raise ValueError("Le nom du client est obligatoire.")
            with connecter() as conn:
                conn.execute("""
                    INSERT INTO clients (nom, telephone, date_creation)
                    VALUES (?, ?, ?)
                """, (nom, e["tel"].text.strip(), date_maintenant()))
            pop.dismiss()
            self.afficher_message("Succès", "Client ajouté.")

        self.formulaire("Ajouter un client", champs, enregistrer)

    def clients_gui(self, *_):
        with connecter() as conn:
            clients = conn.execute(
                "SELECT id, nom, telephone FROM clients ORDER BY nom").fetchall()
        texte = "\n".join(
            f"N°{c[0]} — {c[1]} — {c[2] or 'Sans téléphone'}" for c in clients)
        self.afficher_message("Liste des clients", texte or "Aucun client.")

    # Ventes
    def vente_gui(self, *_args, credit=False):
        with connecter() as conn:
            produits = conn.execute(
                "SELECT id, nom FROM produits ORDER BY nom").fetchall()
            clients = conn.execute(
                "SELECT id, nom FROM clients ORDER BY nom").fetchall()
        if not produits:
            self.afficher_message("Vente", "Ajoute d'abord un produit.")
            return
        if credit and not clients:
            self.afficher_message("Crédit", "Ajoute d'abord un client.")
            return

        def choisir_produit(pid):
            with connecter() as conn:
                produit = conn.execute(
                    "SELECT nom, prix_vente, prix_achat FROM produits WHERE id=?",
                    (pid,)).fetchone()
            if not produit:
                self.afficher_message("Vente", "Produit introuvable.")
                return
            nom, prix_vente, prix_achat = produit

            def continuer(cid=None, nom_client=""):
                champs = [{"key": "qte", "label": "Quantité", "value": "1",
                           "filtre": "int"}]

                def enregistrer(e, pop):
                    qte = entier(e["qte"].text, "Quantité", 1)
                    with connecter() as conn:
                        stock = get_stock_reel(pid, conn)
                        if qte > stock:
                            raise ValueError(f"Stock insuffisant. Disponible : {stock}.")
                        # La vérification et les écritures sont dans la même transaction.
                        if credit:
                            conn.execute("""
                                INSERT INTO ventes
                                (produit_id, date_vente, quantite, prix_unitaire,
                                 cout_unitaire, type_vente)
                                VALUES (?, ?, ?, ?, ?, ?)
                            """, (pid, date_maintenant(), qte, prix_vente,
                                  prix_achat, "credit"))
                            conn.execute("""
                                INSERT INTO dettes
                                (client_id, produit_id, date_dette, quantite,
                                 prix_unitaire, cout_unitaire, montant_total)
                                VALUES (?, ?, ?, ?, ?, ?, ?)
                            """, (cid, pid, date_maintenant(), qte, prix_vente,
                                  prix_achat, qte * prix_vente))
                        else:
                            conn.execute("""
                                INSERT INTO ventes
                                (produit_id, date_vente, quantite, prix_unitaire,
                                 cout_unitaire, type_vente)
                                VALUES (?, ?, ?, ?, ?, ?)
                            """, (pid, date_maintenant(), qte, prix_vente,
                                  prix_achat, "comptant"))
                        stock_restant = stock - qte
                    pop.dismiss()
                    self.afficher_message(
                        "Vente enregistrée",
                        f"Produit : {nom}\n"
                        + (f"Client : {nom_client}\n" if credit else "")
                        + f"Quantité : {qte}\n"
                        f"Prix unitaire : {prix_vente} FCFA\n"
                        f"Total : {qte * prix_vente:.0f} FCFA\n"
                        + ("Vente à crédit : montant ajouté aux dettes.\n" if credit else "")
                        + f"Stock restant : {stock_restant}"
                    )

                self.formulaire(
                    "Vente à crédit" if credit else "Vente au comptant",
                    champs, enregistrer, hauteur=0.55)

            if credit:
                self.choisir(
                    "Choisir le client", clients,
                    lambda cid: continuer(
                        cid, next((n for i, n in clients if i == cid), ""))
                )
            else:
                continuer()

        self.choisir("Choisir le produit", produits, choisir_produit)

    # Caisse rapide : panier multi-produits, vente comptant/crédit et reçu texte.
    def caisse_rapide_gui(self, *_):
        panier = []
        try:
            with connecter() as conn:
                produits = conn.execute(
                    "SELECT id, nom, prix_vente, prix_achat FROM produits ORDER BY nom"
                ).fetchall()
                clients = conn.execute("SELECT id, nom FROM clients ORDER BY nom").fetchall()
        except Exception as exc:
            self.erreur("Caisse rapide", exc)
            return
        if not produits:
            self.afficher_message("Caisse rapide", "Ajoute d'abord un produit au catalogue.")
            return

        box = BoxLayout(orientation="vertical", padding=dp(8), spacing=dp(6))
        recherche = TextInput(hint_text="Rechercher un produit…", multiline=False,
                             size_hint_y=None, height=dp(45))
        box.add_widget(recherche)
        liste_scroll = ScrollView(do_scroll_x=False, do_scroll_y=True)
        liste = BoxLayout(orientation="vertical", size_hint_y=None, spacing=dp(4))
        liste.bind(minimum_height=liste.setter("height"))
        liste_scroll.add_widget(liste)
        box.add_widget(liste_scroll)
        panier_label = Label(text="Panier vide", size_hint_y=None, height=dp(80),
                             halign="left", valign="middle")
        panier_label.bind(size=lambda obj, val: setattr(obj, "text_size", val))
        box.add_widget(panier_label)
        actions = BoxLayout(size_hint_y=None, height=dp(48), spacing=dp(6))
        btn_comptant = Button(text="Valider comptant")
        btn_credit = Button(text="Valider à crédit")
        actions.add_widget(btn_comptant)
        actions.add_widget(btn_credit)
        box.add_widget(actions)
        pop = Popup(title="Caisse rapide — ajouter au panier", content=box,
                    size_hint=(0.96, 0.92), auto_dismiss=True)

        def actualiser_panier():
            if not panier:
                panier_label.text = "Panier vide"
                return
            total = sum(l["qte"] * l["prix"] for l in panier)
            panier_label.text = "PANIER\n" + "\n".join(
                f"{l['nom']} × {l['qte']} = {l['qte']*l['prix']:.0f} FCFA" for l in panier
            ) + f"\nTOTAL : {total:.0f} FCFA"

        def ajouter(pid, nom, prix, cout):
            def saisir_qte(_btn):
                self.formulaire(
                    f"Quantité — {nom}",
                    [{"key": "qte", "label": "Quantité à vendre", "value": "1", "filtre": "int"}],
                    lambda champs, qpop: ajouter_qte_finale(pid, nom, prix, cout, champs, qpop),
                    hauteur=0.48,
                )
            saisir_qte(None)

        def ajouter_qte_finale(pid, nom, prix, cout, champs, qpop):
            qte = entier(champs["qte"].text, "Quantité", 1)
            with connecter() as conn:
                stock = get_stock_reel(pid, conn)
            deja = sum(l["qte"] for l in panier if l["pid"] == pid)
            if qte + deja > stock:
                raise ValueError(f"Stock insuffisant pour {nom}. Disponible : {stock}; déjà dans le panier : {deja}.")
            existant = next((l for l in panier if l["pid"] == pid), None)
            if existant:
                existant["qte"] += qte
            else:
                panier.append({"pid": pid, "nom": nom, "prix": float(prix),
                               "cout": float(cout), "qte": qte})
            qpop.dismiss()
            actualiser_panier()

        def remplir_liste(*_):
            liste.clear_widgets()
            terme = recherche.text.strip().casefold()
            trouves = [x for x in produits if terme in str(x[1]).casefold()]
            for pid, nom, prix, cout in trouves:
                b = Button(text=f"{nom}  •  {float(prix):.0f} FCFA",
                           size_hint_y=None, height=dp(48))
                b.bind(on_release=lambda _b, ident=pid, n=nom, pr=prix, co=cout:
                        ajouter(ident, n, pr, co))
                liste.add_widget(b)
            if not trouves:
                liste.add_widget(Label(text="Aucun produit trouvé", size_hint_y=None, height=dp(40)))
        recherche.bind(text=remplir_liste)
        remplir_liste()

        def finaliser(credit=False):
            if not panier:
                self.afficher_message("Caisse rapide", "Le panier est vide.")
                return
            if credit and not clients:
                self.afficher_message("Vente à crédit", "Ajoute d'abord un client.")
                return
            def enregistrer(cid=None, nom_client=""):
                total = sum(l["qte"] * l["prix"] for l in panier)
                maintenant = date_maintenant()
                try:
                    with connecter() as conn:
                        # Contrôle de stock puis écritures dans une transaction unique.
                        for ligne in panier:
                            stock = get_stock_reel(ligne["pid"], conn)
                            if ligne["qte"] > stock:
                                raise ValueError(f"Stock insuffisant pour {ligne['nom']} : {stock} disponible(s).")
                        for ligne in panier:
                            conn.execute("""INSERT INTO ventes
                                (produit_id, date_vente, quantite, prix_unitaire, cout_unitaire, type_vente)
                                VALUES (?, ?, ?, ?, ?, ?)""",
                                (ligne["pid"], maintenant, ligne["qte"], ligne["prix"],
                                 ligne["cout"], "credit" if credit else "comptant"))
                            if credit:
                                conn.execute("""INSERT INTO dettes
                                    (client_id, produit_id, date_dette, quantite, prix_unitaire, cout_unitaire, montant_total)
                                    VALUES (?, ?, ?, ?, ?, ?, ?)""",
                                    (cid, ligne["pid"], maintenant, ligne["qte"], ligne["prix"],
                                     ligne["cout"], ligne["qte"] * ligne["prix"]))
                    lignes_recu = ["BOUTIQUEFACILE — REÇU DE VENTE", f"Date : {maintenant}",
                                   f"Mode : {'CRÉDIT' if credit else 'COMPTANT'}"]
                    if credit:
                        lignes_recu.append(f"Client : {nom_client}")
                    lignes_recu.append("-" * 34)
                    for ligne in panier:
                        lignes_recu.append(f"{ligne['nom']} x {ligne['qte']} @ {ligne['prix']:.0f} = {ligne['qte']*ligne['prix']:.0f} FCFA")
                    lignes_recu.extend(["-" * 34, f"TOTAL : {total:.0f} FCFA"])
                    if credit:
                        lignes_recu.append("Montant à recevoir ultérieurement.")
                    recu = "\n".join(lignes_recu)
                    chemin = None
                    try:
                        os.makedirs(BACKUP_DIR, exist_ok=True)
                        nom_fichier = "Recu_BoutiqueFacile_" + datetime.now().strftime("%Y%m%d_%H%M%S") + ".txt"
                        chemin = os.path.join(BACKUP_DIR, nom_fichier)
                        with open(chemin, "w", encoding="utf-8") as f:
                            f.write(recu)
                    except Exception:
                        pass
                    pop.dismiss()
                    self.actualiser_tableau_de_bord()
                    self.afficher_message("Vente enregistrée", recu + (f"\n\nReçu enregistré : {chemin}" if chemin else "\n\nLe reçu n'a pas pu être enregistré dans Téléchargements."))
                except Exception as exc:
                    self.erreur("Échec de la vente", exc)
            if credit:
                pop.dismiss()
                self.choisir("Choisir le client", clients,
                    lambda cid: enregistrer(cid, next((n for i, n in clients if i == cid), "")))
            else:
                enregistrer()
        btn_comptant.bind(on_release=lambda *_: finaliser(False))
        btn_credit.bind(on_release=lambda *_: finaliser(True))
        pop.open()

    # Entrées de stock
    def entree_stock_gui(self, *_):
        with connecter() as conn:
            produits = conn.execute(
                "SELECT id, nom FROM produits ORDER BY nom").fetchall()
        if not produits:
            self.afficher_message("Stock", "Aucun produit.")
            return

        def choisir_produit(pid):
            champs = [
                {"key": "qte", "label": "Quantité reçue", "value": "1",
                 "filtre": "int"},
                {"key": "cout", "label": "Coût unitaire", "value": "0"},
            ]

            def enregistrer(e, pop):
                qte = entier(e["qte"].text, "Quantité", 1)
                cout = nombre(e["cout"].text, "Coût unitaire")
                with connecter() as conn:
                    conn.execute("""
                        INSERT INTO entrees_stock
                        (produit_id, date_entree, quantite, cout_unitaire)
                        VALUES (?, ?, ?, ?)
                    """, (pid, date_maintenant(), qte, cout))
                    stock = get_stock_reel(pid, conn)
                pop.dismiss()
                self.afficher_message("Stock mis à jour", f"Nouveau stock : {stock}")

            self.formulaire("Entrée de stock", champs, enregistrer)

        self.choisir("Choisir un produit", produits, choisir_produit)

    # Dépenses
    def depense_gui(self, *_):
        champs = [
            {"key": "cat", "label": "Catégorie", "value": "autre"},
            {"key": "desc", "label": "Description"},
            {"key": "mont", "label": "Montant", "value": "0"},
        ]

        def enregistrer(e, pop):
            categorie = e["cat"].text.strip() or "autre"
            description = e["desc"].text.strip()
            valeur = nombre(e["mont"].text, "Montant", 0.01)
            with connecter() as conn:
                conn.execute("""
                    INSERT INTO depenses (date_depense, categorie, description, montant)
                    VALUES (?, ?, ?, ?)
                """, (date_maintenant(), categorie, description, valeur))
            pop.dismiss()
            self.afficher_message("Succès", "Dépense enregistrée.")

        self.formulaire("Nouvelle dépense", champs, enregistrer)

    # Dettes et remboursements
    def dettes_gui(self, *_):
        with connecter() as conn:
            rows = conn.execute("""
                SELECT d.id, c.nom, d.montant_total,
                       COALESCE(SUM(r.montant),0)
                FROM dettes d JOIN clients c ON c.id=d.client_id
                LEFT JOIN remboursements r ON r.dette_id=d.id
                GROUP BY d.id ORDER BY d.id DESC
            """).fetchall()
        lignes = [
            f"Dette N°{r[0]} — {r[1]}\nTotal : {r[2]:.0f} FCFA\n"
            f"Remboursé : {r[3]:.0f} FCFA\nReste : {r[2]-r[3]:.0f} FCFA\n"
            + "-" * 20 for r in rows
        ]
        self.afficher_message("Dettes des clients", "\n".join(lignes) or "Aucune dette.")

    def remboursement_gui(self, *_):
        with connecter() as conn:
            rows = conn.execute("""
                SELECT d.id, c.nom, d.montant_total,
                       COALESCE(SUM(r.montant),0)
                FROM dettes d JOIN clients c ON c.id=d.client_id
                LEFT JOIN remboursements r ON r.dette_id=d.id
                GROUP BY d.id
                HAVING d.montant_total > COALESCE(SUM(r.montant),0)
            """).fetchall()
        if not rows:
            self.afficher_message("Remboursements", "Aucune dette à rembourser.")
            return

        options = [(r[0], f"N°{r[0]} — {r[1]} — reste {r[2]-r[3]:.0f} FCFA")
                   for r in rows]

        def choisir_dette(did):
            champs = [{"key": "mont", "label": "Montant à rembourser", "value": "1"}]

            def enregistrer(e, pop):
                somme = nombre(e["mont"].text, "Montant", 0.01)
                with connecter() as conn:
                    dette = conn.execute("""
                        SELECT d.montant_total, COALESCE(SUM(r.montant),0)
                        FROM dettes d LEFT JOIN remboursements r ON r.dette_id=d.id
                        WHERE d.id=? GROUP BY d.id
                    """, (did,)).fetchone()
                    if not dette:
                        raise ValueError("Dette introuvable.")
                    reste = dette[0] - dette[1]
                    if somme > reste:
                        raise ValueError(f"Le reste dû est de {reste:.0f} FCFA.")
                    conn.execute("""
                        INSERT INTO remboursements (dette_id, date_remboursement, montant)
                        VALUES (?, ?, ?)
                    """, (did, date_maintenant(), somme))
                pop.dismiss()
                self.afficher_message("Succès", f"Remboursement : {somme:.0f} FCFA")

            self.formulaire("Remboursement", champs, enregistrer, hauteur=0.5)

        self.choisir("Choisir une dette", options, choisir_dette)

    # Bilans
    def bilan_jour_gui(self, *_):
        jour = date.today().strftime("%Y-%m-%d")
        with connecter() as conn:
            ca, cout, nb = conn.execute("""
                SELECT COALESCE(SUM(quantite*prix_unitaire),0),
                       COALESCE(SUM(quantite*cout_unitaire),0), COUNT(*)
                FROM ventes WHERE date(date_vente)=?
            """, (jour,)).fetchone()
            dep = conn.execute("""
                SELECT COALESCE(SUM(montant),0) FROM depenses
                WHERE date(date_depense)=?
            """, (jour,)).fetchone()[0]
            remb = conn.execute("""
                SELECT COALESCE(SUM(montant),0) FROM remboursements
                WHERE date(date_remboursement)=?
            """, (jour,)).fetchone()[0]
        marge = ca - cout
        self.afficher_message(
            f"Bilan du {jour}",
            f"Nombre de lignes de vente : {nb}\n"
            f"Chiffre d'affaires enregistré : {ca:.0f} FCFA\n"
            f"Coût des produits vendus : {cout:.0f} FCFA\n"
            f"Marge brute : {marge:.0f} FCFA\n"
            f"Dépenses : {dep:.0f} FCFA\n"
            f"Résultat estimé : {marge-dep:.0f} FCFA\n"
            f"Remboursements reçus : {remb:.0f} FCFA\n\n"
            "Attention : le chiffre d'affaires comprend aussi les ventes à crédit."
        )

    def bilan_mensuel_gui(self, *_):
        champs = [{"key": "mois", "label": "Mois (AAAA-MM)",
                   "value": date.today().strftime("%Y-%m")}]

        def calculer(e, pop):
            mois = e["mois"].text.strip()
            try:
                datetime.strptime(mois, "%Y-%m")
            except ValueError:
                raise ValueError("Utilise le format AAAA-MM.")
            with connecter() as conn:
                ca, cout, nb = conn.execute("""
                    SELECT COALESCE(SUM(quantite*prix_unitaire),0),
                           COALESCE(SUM(quantite*cout_unitaire),0), COUNT(*)
                    FROM ventes WHERE substr(date_vente,1,7)=?
                """, (mois,)).fetchone()
                dep = conn.execute("""
                    SELECT COALESCE(SUM(montant),0) FROM depenses
                    WHERE substr(date_depense,1,7)=?
                """, (mois,)).fetchone()[0]
                remb = conn.execute("""
                    SELECT COALESCE(SUM(montant),0) FROM remboursements
                    WHERE substr(date_remboursement,1,7)=?
                """, (mois,)).fetchone()[0]
            pop.dismiss()
            marge = ca - cout
            self.afficher_message(
                f"Bilan de {mois}",
                f"Nombre de lignes de vente : {nb}\n"
                f"Chiffre d'affaires enregistré : {ca:.0f} FCFA\n"
                f"Coût des produits vendus : {cout:.0f} FCFA\n"
                f"Marge brute : {marge:.0f} FCFA\n"
                f"Dépenses : {dep:.0f} FCFA\n"
                f"Résultat estimé : {marge-dep:.0f} FCFA\n"
                f"Remboursements reçus : {remb:.0f} FCFA\n\n"
                "Le chiffre d'affaires inclut les ventes à crédit."
            )

        self.formulaire("Bilan mensuel", champs, calculer, hauteur=0.5)

    # Sécurité locale : code PIN haché, jamais enregistré en clair.
    def _charger_securite(self):
        if not os.path.isfile(SECURITY_FILE):
            return None
        with open(SECURITY_FILE, "r", encoding="utf-8") as fichier:
            donnees = json.load(fichier)
        if not isinstance(donnees, dict) or not donnees.get("salt") or not donnees.get("hash"):
            raise ValueError("Le fichier de sécurité est invalide. Ne le supprime pas sans avoir sauvegardé tes données.")
        return donnees

    @staticmethod
    def _hachage_pin(pin, salt):
        return hashlib.pbkdf2_hmac("sha256", pin.encode("utf-8"), bytes.fromhex(salt), 200000).hex()

    def _enregistrer_pin(self, pin):
        if not pin.isdigit() or not 4 <= len(pin) <= 8:
            raise ValueError("Le code PIN doit contenir de 4 à 8 chiffres.")
        salt = secrets.token_hex(16)
        donnees = {"salt": salt, "hash": self._hachage_pin(pin, salt)}
        temporaire = SECURITY_FILE + ".tmp"
        with open(temporaire, "w", encoding="utf-8") as fichier:
            json.dump(donnees, fichier)
        os.replace(temporaire, SECURITY_FILE)

    def demander_pin(self, *_):
        try:
            securite = self._charger_securite()
        except Exception as exc:
            self.afficher_message("Erreur de sécurité", str(exc))
            return

        configuration = securite is None
        box = BoxLayout(orientation="vertical", padding=dp(14), spacing=dp(10))
        if configuration:
            message = "Crée un code PIN de 4 à 8 chiffres pour protéger l’ouverture de BoutiqueFacile.\nMémorise-le : il sera nécessaire à chaque ouverture."
        else:
            message = "Entre ton code PIN pour accéder à BoutiqueFacile."
        box.add_widget(Label(text=message, size_hint_y=None, height=dp(82), halign="left", valign="middle"))
        pin = TextInput(password=True, hint_text="Code PIN (4 à 8 chiffres)", multiline=False,
                        input_filter="int", size_hint_y=None, height=dp(48))
        box.add_widget(pin)
        confirmation = None
        if configuration:
            confirmation = TextInput(password=True, hint_text="Confirme le code PIN", multiline=False,
                                     input_filter="int", size_hint_y=None, height=dp(48))
            box.add_widget(confirmation)
        bouton = Button(text="Créer le code PIN" if configuration else "Déverrouiller",
                        size_hint_y=None, height=dp(48))
        box.add_widget(bouton)
        popup = Popup(title="Sécurité BoutiqueFacile", content=box,
                      size_hint=(0.92, None), height=dp(330 if configuration else 250),
                      auto_dismiss=False)
        self._pin_popup = popup

        def valider(*_):
            valeur = pin.text.strip()
            try:
                if configuration:
                    if valeur != confirmation.text.strip():
                        raise ValueError("Les deux codes PIN ne correspondent pas.")
                    self._enregistrer_pin(valeur)
                    popup.dismiss()
                    self.afficher_message("Code PIN créé", "La protection est activée. Garde ton code PIN en lieu sûr.")
                else:
                    donnees = self._charger_securite()
                    attendu = self._hachage_pin(valeur, donnees["salt"])
                    if not secrets.compare_digest(attendu, donnees["hash"]):
                        raise ValueError("Code PIN incorrect. Réessaie.")
                    popup.dismiss()
            except Exception as exc:
                self.erreur("Vérification du code PIN", exc)
        bouton.bind(on_release=valider)
        popup.open()
        Clock.schedule_once(lambda _dt: setattr(pin, "focus", True), 0.3)

    def sauvegarde_automatique(self, *_):
        """Crée une copie cohérente SQLite et garde les 10 plus récentes."""
        try:
            if not os.path.isfile(DB):
                return
            os.makedirs(BACKUP_DIR, exist_ok=True)
            horodatage = datetime.now().strftime("%Y%m%d_%H%M%S")
            destination = os.path.join(BACKUP_DIR, f"boutiquefacile_auto_{horodatage}.db")
            with sqlite3.connect(DB, timeout=15) as source, sqlite3.connect(destination) as copie:
                source.backup(copie)
            copies = sorted(
                (os.path.join(BACKUP_DIR, nom) for nom in os.listdir(BACKUP_DIR)
                 if nom.startswith("boutiquefacile_auto_") and nom.endswith(".db")),
                key=os.path.getmtime, reverse=True
            )
            for ancienne in copies[MAX_AUTO_BACKUPS:]:
                try:
                    os.remove(ancienne)
                except OSError:
                    pass
        except Exception as exc:
            # Une sauvegarde ratée ne doit pas empêcher l’ouverture de l’application.
            print("Sauvegarde automatique impossible :", exc)

    # Sauvegarde
    def sauvegarder_gui(self, *_):
        try:
            os.makedirs(BACKUP_DIR, exist_ok=True)
            destination = os.path.join(
                BACKUP_DIR,
                "boutiquefacile_" + datetime.now().strftime("%Y%m%d_%H%M%S") + ".db"
            )
            with connecter() as source:
                with sqlite3.connect(destination) as copie:
                    source.backup(copie)
            self.afficher_message("Sauvegarde", f"Copie créée :\n{destination}")
        except Exception as exc:
            self.erreur("Sauvegarde", exc)



    # Sécurité : sauvegarde et restauration de la base de données
    def sauvegarder_base_gui(self, *_):
        import shutil
        from datetime import datetime
        try:
            if not os.path.isfile(DB):
                self.afficher_message(
                    "Sauvegarde impossible",
                    "La base de données existante est introuvable :\n" + DB
                )
                return

            dossier = "/storage/emulated/0/Download"
            os.makedirs(dossier, exist_ok=True)
            horodatage = datetime.now().strftime("%Y%m%d_%H%M%S")
            destination = os.path.join(
                dossier, f"boutiquefacile_sauvegarde_{horodatage}.db"
            )
            # SQLite backup API: makes a consistent copy even if SQLite is in use.
            with sqlite3.connect(DB) as source, sqlite3.connect(destination) as copie:
                source.backup(copie)

            self.afficher_message(
                "Sauvegarde réussie",
                "Une copie de la base a été enregistrée ici :\n\n"
                + destination
                + "\n\nConserve ce fichier dans un endroit sûr."
            )
        except Exception as exc:
            self.erreur("Sauvegarde", exc)

    def restaurer_base_gui(self, *_):
        from kivy.uix.textinput import TextInput
        from kivy.uix.boxlayout import BoxLayout
        from kivy.uix.button import Button
        from kivy.uix.popup import Popup

        contenu = BoxLayout(orientation="vertical", spacing=dp(8), padding=dp(10))
        contenu.add_widget(Label(
            text="Colle le chemin complet du fichier .db de sauvegarde.\n"
                 "Exemple : /storage/emulated/0/Download/boutiquefacile_sauvegarde.db",
            size_hint_y=None, height=dp(58)
        ))
        chemin = TextInput(
            hint_text="Chemin complet du fichier .db",
            multiline=False, size_hint_y=None, height=dp(48)
        )
        contenu.add_widget(chemin)
        boutons = BoxLayout(size_hint_y=None, height=dp(48), spacing=dp(8))
        popup = Popup(
            title="Restaurer une sauvegarde ?",
            content=contenu, size_hint=(0.94, None), height=dp(230),
            auto_dismiss=False
        )

        def confirmer(*_):
            import shutil
            fichier = chemin.text.strip()
            if not fichier or not os.path.isfile(fichier):
                self.afficher_message(
                    "Fichier introuvable",
                    "Vérifie le chemin du fichier de sauvegarde .db."
                )
                return
            if not fichier.lower().endswith(".db"):
                self.afficher_message(
                    "Fichier invalide",
                    "Choisis un fichier de sauvegarde avec l'extension .db."
                )
                return
            try:
                # Validate the candidate DB before touching the active one.
                with sqlite3.connect(f"file:{fichier}?mode=ro", uri=True) as test:
                    result = test.execute("PRAGMA quick_check").fetchone()
                    if not result or result[0] != "ok":
                        raise ValueError("La base de sauvegarde semble endommagée.")
                    tables = {
                        row[0] for row in test.execute(
                            "SELECT name FROM sqlite_master WHERE type='table'"
                        )
                    }
                required = {"produits", "ventes"}
                if not required.issubset(tables):
                    raise ValueError(
                        "Ce fichier ne semble pas être une base BoutiqueFacile compatible."
                    )

                # Keep a safety copy of the current DB before restoring.
                import datetime as _datetime
                dossier = "/storage/emulated/0/Download"
                os.makedirs(dossier, exist_ok=True)
                avant = os.path.join(
                    dossier,
                    "boutiquefacile_avant_restauration_"
                    + _datetime.datetime.now().strftime("%Y%m%d_%H%M%S") + ".db"
                )
                with sqlite3.connect(DB) as actuelle, sqlite3.connect(avant) as copie:
                    actuelle.backup(copie)
                with sqlite3.connect(fichier) as source, sqlite3.connect(DB) as cible:
                    source.backup(cible)

                popup.dismiss()
                self.afficher_message(
                    "Restauration terminée",
                    "La sauvegarde a été restaurée.\n\n"
                    "Une copie de sécurité de l'ancienne base est disponible ici :\n"
                    + avant
                    + "\n\nFerme puis relance l'application pour vérifier les données."
                )
            except Exception as exc:
                self.erreur("Restauration", exc)

        annuler = Button(text="Annuler")
        annuler.bind(on_release=popup.dismiss)
        appliquer = Button(text="Restaurer")
        appliquer.bind(on_release=confirmer)
        boutons.add_widget(annuler)
        boutons.add_widget(appliquer)
        contenu.add_widget(boutons)
        popup.open()

    # Nouvelles fonctions : alertes de stock faible et historique des ventes
    def alertes_stock_gui(self, *_):
        try:
            lignes = []
            with connecter() as conn:
                produits = conn.execute(
                    "SELECT id, nom, stock_minimum FROM produits ORDER BY nom"
                ).fetchall()
                for pid, nom, minimum in produits:
                    stock = get_stock_reel(pid, conn)
                    minimum = minimum if minimum is not None else 0
                    if stock <= minimum:
                        lignes.append(
                            f"⚠ {nom}\n   Stock restant : {stock} | Seuil minimum : {minimum}"
                        )
            if lignes:
                self.afficher_message(
                    "Alertes de stock faible",
                    "Les produits suivants doivent être réapprovisionnés :\n\n"
                    + "\n\n".join(lignes)
                )
            else:
                self.afficher_message(
                    "Stock en bon état",
                    "Aucun produit n'est au-dessous de son seuil minimum."
                )
        except Exception as exc:
            self.erreur("Alertes de stock", exc)

    def historique_ventes_gui(self, *_):
        try:
            with connecter() as conn:
                lignes = conn.execute("""
                    SELECT v.date_vente, p.nom, v.quantite, v.prix_unitaire,
                           v.cout_unitaire, v.type_vente
                    FROM ventes v
                    LEFT JOIN produits p ON p.id = v.produit_id
                    ORDER BY v.id DESC
                    LIMIT 100
                """).fetchall()
            if not lignes:
                self.afficher_message("Historique des ventes", "Aucune vente enregistrée.")
                return

            texte = []
            total_ca = 0
            total_marge = 0
            for date_vente, nom, qte, prix, cout, type_vente in lignes:
                nom = nom or "Produit supprimé"
                ca = (qte or 0) * (prix or 0)
                marge = (qte or 0) * ((prix or 0) - (cout or 0))
                total_ca += ca
                total_marge += marge
                mode = "Crédit" if type_vente == "credit" else "Comptant"
                texte.append(
                    f"{date_vente or 'Date inconnue'}\n"
                    f"{nom} × {qte} — {mode}\n"
                    f"Total : {ca:.0f} FCFA | Marge brute : {marge:.0f} FCFA"
                )
            self.afficher_message(
                "Historique des 100 dernières ventes",
                "\n\n".join(texte)
                + f"\n\nTOTAL affiché : {total_ca:.0f} FCFA"
                + f"\nMarge brute affichée : {total_marge:.0f} FCFA"
            )
        except Exception as exc:
            self.erreur("Historique des ventes", exc)

    # Fournisseurs : table additive, sans toucher aux données existantes.
    def initialiser_fournisseurs(self):
        with connecter() as conn:
            conn.execute("""CREATE TABLE IF NOT EXISTS fournisseurs (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                nom TEXT NOT NULL,
                telephone TEXT DEFAULT '',
                description TEXT DEFAULT '',
                date_creation TEXT NOT NULL
            )""")

    def ajouter_fournisseur_gui(self, *_):
        try:
            self.initialiser_fournisseurs()
        except Exception as exc:
            self.erreur("Fournisseurs", exc); return
        champs = [
            {"key":"nom", "label":"Nom du fournisseur", "value":""},
            {"key":"telephone", "label":"Téléphone", "value":""},
            {"key":"description", "label":"Notes (facultatif)", "value":""},
        ]
        def enregistrer(e, pop):
            nom=e['nom'].text.strip()
            if not nom: raise ValueError("Le nom du fournisseur est obligatoire.")
            with connecter() as conn:
                conn.execute("INSERT INTO fournisseurs(nom,telephone,description,date_creation) VALUES(?,?,?,?)",
                    (nom,e['telephone'].text.strip(),e['description'].text.strip(),date_maintenant()))
            pop.dismiss(); self.afficher_message("Fournisseur ajouté", f"{nom} a été enregistré.")
        self.formulaire("Ajouter un fournisseur", champs, enregistrer, hauteur=0.68)

    def fournisseurs_gui(self, *_):
        try:
            self.initialiser_fournisseurs()
            with connecter() as conn:
                rows=conn.execute("SELECT nom,telephone,description,date_creation FROM fournisseurs ORDER BY nom").fetchall()
            if not rows:
                self.afficher_message("Fournisseurs", "Aucun fournisseur enregistré pour le moment.")
                return
            self.afficher_message("Fournisseurs enregistrés", "\n\n".join(
                f"{n}\nTéléphone : {t or '—'}\nNotes : {d or '—'}\nAjouté le : {dt}" for n,t,d,dt in rows))
        except Exception as exc: self.erreur("Fournisseurs", exc)

    # Export CSV lisible dans un tableur : ventes, produits, dépenses, dettes et bilan.
    def exporter_rapports_gui(self, *_):
        try:
            os.makedirs(BACKUP_DIR, exist_ok=True)
            stamp=datetime.now().strftime("%Y%m%d_%H%M%S")
            exports=[]
            with connecter() as conn:
                datasets = {
                    "ventes": ("SELECT v.id,v.date_vente,p.nom,v.quantite,v.prix_unitaire,v.cout_unitaire,v.type_vente FROM ventes v LEFT JOIN produits p ON p.id=v.produit_id ORDER BY v.id", ["id","date","produit","quantite","prix_unitaire_FCFA","cout_unitaire_FCFA","mode"]),
                    "stock": ("SELECT p.id,p.nom,p.categorie,p.prix_achat,p.prix_vente,p.stock_initial + COALESCE((SELECT SUM(e.quantite) FROM entrees_stock e WHERE e.produit_id=p.id),0) - COALESCE((SELECT SUM(v.quantite) FROM ventes v WHERE v.produit_id=p.id),0) AS stock_reel,p.stock_minimum FROM produits p ORDER BY p.nom", ["id","produit","categorie","prix_achat_FCFA","prix_vente_FCFA","stock_reel","stock_minimum"]),
                    "depenses": ("SELECT id,date_depense,categorie,description,montant FROM depenses ORDER BY id", ["id","date","categorie","description","montant_FCFA"]),
                    "dettes": ("SELECT d.id,c.nom,p.nom,d.date_dette,d.quantite,d.prix_unitaire,d.montant_total-COALESCE((SELECT SUM(r.montant) FROM remboursements r WHERE r.dette_id=d.id),0) FROM dettes d LEFT JOIN clients c ON c.id=d.client_id LEFT JOIN produits p ON p.id=d.produit_id ORDER BY d.id", ["id","client","produit","date","quantite","prix_unitaire_FCFA","reste_a_payer_FCFA"]),
                }
                for nom,(sql,headers) in datasets.items():
                    rows=conn.execute(sql).fetchall()
                    path=os.path.join(BACKUP_DIR,f"BoutiqueFacile_{nom}_{stamp}.csv")
                    with open(path,"w",newline="",encoding="utf-8-sig") as f:
                        w=csv.writer(f,delimiter=';'); w.writerow(headers); w.writerows(rows)
                    exports.append(path)
                ca,cout=conn.execute("SELECT COALESCE(SUM(quantite*prix_unitaire),0),COALESCE(SUM(quantite*cout_unitaire),0) FROM ventes").fetchone()
                dep=conn.execute("SELECT COALESCE(SUM(montant),0) FROM depenses").fetchone()[0]
                reste=conn.execute("SELECT COALESCE(SUM(d.montant_total-COALESCE((SELECT SUM(r.montant) FROM remboursements r WHERE r.dette_id=d.id),0)),0) FROM dettes d").fetchone()[0]
            path=os.path.join(BACKUP_DIR,f"BoutiqueFacile_bilan_global_{stamp}.csv")
            with open(path,"w",newline="",encoding="utf-8-sig") as f:
                w=csv.writer(f,delimiter=';'); w.writerow(["indicateur","montant_FCFA"])
                w.writerows([["Chiffre_affaires_enregistre",ca],["Cout_produits_vendus",cout],["Marge_brute",ca-cout],["Depenses",dep],["Resultat_estime",ca-cout-dep],["Reste_a_recevoir_dettes",reste]])
            exports.append(path)
            self.afficher_message("Export terminé", "Les rapports CSV sont dans Téléchargements :\n\n"+"\n\n".join(exports))
        except Exception as exc: self.erreur("Export des rapports", exc)

    # Graphique simple dessiné par Kivy, sans bibliothèque supplémentaire.
    def graphiques_gui(self, *_):
        from kivy.graphics import Color, Line, Rectangle
        from kivy.uix.widget import Widget
        from kivy.uix.gridlayout import GridLayout
        with connecter() as conn:
            ventes=conn.execute("SELECT date(date_vente),COALESCE(SUM(quantite*prix_unitaire),0) FROM ventes WHERE date(date_vente)>=date('now','-6 days') GROUP BY date(date_vente) ORDER BY date(date_vente)").fetchall()
            depenses=conn.execute("SELECT date(date_depense),COALESCE(SUM(montant),0) FROM depenses WHERE date(date_depense)>=date('now','-6 days') GROUP BY date(date_depense) ORDER BY date(date_depense)").fetchall()
        jours=[(date.today()-__import__('datetime').timedelta(days=i)).strftime('%Y-%m-%d') for i in range(6,-1,-1)]
        vm=dict(ventes); dm=dict(depenses); vs=[float(vm.get(j,0) or 0) for j in jours]; ds=[float(dm.get(j,0) or 0) for j in jours]
        maxv=max(vs+ds+[1])
        box=BoxLayout(orientation='vertical',padding=dp(10),spacing=dp(8))
        box.add_widget(Label(text="7 DERNIERS JOURS — VENTES ET DÉPENSES",size_hint_y=None,height=dp(44),font_size='14sp'))
        graph=Widget(size_hint_y=1)
        def dessiner(*_):
            graph.canvas.clear()
            with graph.canvas:
                Color(0.88,0.91,0.93,1); Rectangle(pos=graph.pos,size=graph.size)
                marge=dp(24); largeur=max(dp(1),graph.width-2*marge); hauteur=max(dp(1),graph.height-2*marge)
                Color(0.15,0.45,0.72,1); Line(points=[graph.x+marge,graph.y+marge,graph.x+marge,graph.top-marge,graph.right-marge,graph.top-marge],width=1.2)
                for i,(v,d) in enumerate(zip(vs,ds)):
                    x=graph.x+marge+(i+0.5)*largeur/7
                    bw=largeur/22
                    hv=hauteur*v/maxv; hd=hauteur*d/maxv
                    Color(0.10,0.55,0.35,1); Rectangle(pos=(x-bw-1,graph.y+marge),size=(bw,hv))
                    Color(0.82,0.39,0.12,1); Rectangle(pos=(x+1,graph.y+marge),size=(bw,hd))
        graph.bind(size=dessiner,pos=dessiner); dessiner()
        box.add_widget(graph)
        box.add_widget(Label(text="Vert = ventes • Orange = dépenses\n"+"   ".join(j[5:] for j in jours),size_hint_y=None,height=dp(48),font_size='11sp'))
        box.add_widget(Label(text=f"Total ventes : {sum(vs):,.0f} FCFA   |   Dépenses : {sum(ds):,.0f} FCFA".replace(',',' '),size_hint_y=None,height=dp(38)))
        pop=Popup(title="Graphiques de la semaine",content=box,size_hint=(0.96,0.85)); pop.open()

    def statistiques_tableau_de_bord(self):
        """Calcule des indicateurs depuis la base existante, sans modifier les données."""
        aujourd_hui = date.today().strftime("%Y-%m-%d")
        with connecter() as conn:
            ca, cout = conn.execute("""
                SELECT COALESCE(SUM(quantite * prix_unitaire), 0),
                       COALESCE(SUM(quantite * cout_unitaire), 0)
                FROM ventes WHERE substr(date_vente, 1, 10)=?
            """, (aujourd_hui,)).fetchone()
            depenses = conn.execute("""
                SELECT COALESCE(SUM(montant), 0) FROM depenses
                WHERE substr(date_depense, 1, 10)=?
            """, (aujourd_hui,)).fetchone()[0]
            nombre_produits = conn.execute("SELECT COUNT(*) FROM produits").fetchone()[0]
            produits = conn.execute("SELECT id, stock_initial, stock_minimum FROM produits").fetchall()
            stock_faible = 0
            for pid, stock_initial, seuil in produits:
                stock = get_stock_reel(pid, conn)
                if stock <= (seuil or 0):
                    stock_faible += 1
            dettes = conn.execute("""
                SELECT COALESCE(SUM(
                    d.montant_total - COALESCE((
                        SELECT SUM(r.montant) FROM remboursements r WHERE r.dette_id=d.id
                    ), 0)
                ), 0) FROM dettes d
            """).fetchone()[0]
        return {
            "ca": float(ca or 0), "cout": float(cout or 0),
            "depenses": float(depenses or 0),
            "resultat": float(ca or 0) - float(cout or 0) - float(depenses or 0),
            "produits": int(nombre_produits or 0),
            "stock_faible": int(stock_faible), "dettes": float(dettes or 0),
        }

    def ajouter_carte_indicateur(self, grille, titre, valeur, couleur):
        carte = BoxLayout(orientation="vertical", padding=dp(9), spacing=dp(3))
        with carte.canvas.before:
            from kivy.graphics import Color, RoundedRectangle
            Color(*couleur)
            fond = RoundedRectangle(pos=carte.pos, size=carte.size, radius=[dp(12)])
        carte.bind(pos=lambda obj, val: setattr(fond, "pos", val),
                   size=lambda obj, val: setattr(fond, "size", val))
        carte.add_widget(Label(text=titre, font_size="12sp", color=(1, 1, 1, 0.95),
                               halign="left", valign="middle"))
        valeur_label = Label(text=valeur, font_size="17sp", bold=True,
                             color=(1, 1, 1, 1), halign="left", valign="middle")
        carte.add_widget(valeur_label)
        self.dashboard_labels[titre] = valeur_label
        grille.add_widget(carte)

    def actualiser_tableau_de_bord(self, *_):
        """Actualise les indicateurs sans fermer ni relancer l'application."""
        try:
            stats = self.statistiques_tableau_de_bord()
            valeurs = {
                "VENTES DU JOUR": f"{stats['ca']:,.0f} FCFA".replace(",", " "),
                "RÉSULTAT ESTIMÉ DU JOUR": f"{stats['resultat']:,.0f} FCFA".replace(",", " "),
                "PRODUITS EN CATALOGUE": str(stats["produits"]),
                "STOCK FAIBLE / ÉPUISÉ": str(stats["stock_faible"]),
                "DÉPENSES DU JOUR": f"{stats['depenses']:,.0f} FCFA".replace(",", " "),
                "RESTE À RECEVOIR (DETTES)": f"{stats['dettes']:,.0f} FCFA".replace(",", " "),
            }
            for titre, valeur in valeurs.items():
                etiquette = self.dashboard_labels.get(titre)
                if etiquette is not None:
                    etiquette.text = valeur
        except Exception:
            # Une erreur temporaire ne doit pas fermer l'application ni afficher
            # une fenêtre d'erreur toutes les quelques secondes.
            pass

    # Tableau de bord sombre, accents émeraude et navigation inférieure.
    def construire_interface(self):
        from kivy.graphics import Color, RoundedRectangle
        from kivy.uix.image import Image

        page = BoxLayout(orientation="vertical", spacing=dp(0), padding=0)
        zone = ScrollView(do_scroll_x=False, do_scroll_y=True, bar_width=dp(4),
                          scroll_type=["bars", "content"])
        contenu = BoxLayout(orientation="vertical", size_hint_y=None,
                            padding=[dp(14), dp(14), dp(14), dp(20)], spacing=dp(12))
        contenu.bind(minimum_height=contenu.setter("height"))
        with contenu.canvas.before:
            Color(0.035, 0.055, 0.10, 1)
            fond = RoundedRectangle(pos=contenu.pos, size=contenu.size)
        contenu.bind(pos=lambda o,v: setattr(fond,"pos",v), size=lambda o,v: setattr(fond,"size",v))

        entete = BoxLayout(orientation="horizontal", size_hint_y=None, height=dp(82), spacing=dp(12), padding=dp(12))
        with entete.canvas.before:
            Color(0.065, 0.095, 0.17, 1)
            ef = RoundedRectangle(pos=entete.pos, size=entete.size, radius=[dp(18)])
        entete.bind(pos=lambda o,v: setattr(ef,"pos",v), size=lambda o,v: setattr(ef,"size",v))
        logo = os.path.join(os.path.dirname(__file__), "boutiquefacile_logo.png")
        if os.path.exists(logo):
            entete.add_widget(Image(source=logo, size_hint=(None, 1), width=dp(58), allow_stretch=True, keep_ratio=True))
        titres = BoxLayout(orientation="vertical", spacing=dp(2))
        titres.add_widget(Label(text="[b]BOUTIQUEFACILE[/b]", markup=True, font_size="22sp", color=(0.96,0.98,1,1), halign="left", valign="middle"))
        titres.add_widget(Label(text="Votre boutique, sous contrôle.", font_size="12sp", color=(0.48,0.91,0.72,1), halign="left", valign="middle"))
        entete.add_widget(titres)
        contenu.add_widget(entete)

        try:
            verifier_structure()
            with connecter() as conn:
                conn.execute("CREATE TABLE IF NOT EXISTS fournisseurs (id INTEGER PRIMARY KEY AUTOINCREMENT, nom TEXT NOT NULL, telephone TEXT DEFAULT '', description TEXT DEFAULT '', date_creation TEXT NOT NULL)")
            statut, sc = "● Base de données prête", (0.25,0.88,0.62,1)
        except Exception as exc:
            statut, sc = "⚠ Vérification nécessaire : " + str(exc), (1,0.45,0.38,1)
        contenu.add_widget(Label(text=statut, size_hint_y=None, height=dp(28), font_size="12sp", color=sc, halign="left", valign="middle"))
        contenu.add_widget(Label(text="[b]APERÇU DE L’ACTIVITÉ[/b]", markup=True, size_hint_y=None, height=dp(30), font_size="16sp", color=(0.92,0.96,1,1), halign="left", valign="middle"))
        grille = GridLayout(cols=2, spacing=dp(9), size_hint_y=None, row_default_height=dp(88), row_force_default=True)
        grille.bind(minimum_height=grille.setter("height"))
        try:
            stats = self.statistiques_tableau_de_bord()
            indicateurs = [("VENTES DU JOUR",f"{stats['ca']:,.0f} FCFA".replace(","," "),(0.05,0.55,0.39,1)),
                ("RÉSULTAT ESTIMÉ",f"{stats['resultat']:,.0f} FCFA".replace(","," "),(0.08,0.35,0.30,1)),
                ("PRODUITS",str(stats["produits"]),(0.12,0.24,0.42,1)),
                ("STOCK FAIBLE",str(stats["stock_faible"]),(0.45,0.23,0.19,1)),
                ("DÉPENSES DU JOUR",f"{stats['depenses']:,.0f} FCFA".replace(","," "),(0.27,0.22,0.40,1)),
                ("DETTES À RECEVOIR",f"{stats['dettes']:,.0f} FCFA".replace(","," "),(0.10,0.31,0.39,1))]
            for t,v,c in indicateurs: self.ajouter_carte_indicateur(grille,t,v,c)
        except Exception:
            self.ajouter_carte_indicateur(grille,"TABLEAU DE BORD","Indisponible",(0.45,0.23,0.19,1))
        contenu.add_widget(grille)
        contenu.add_widget(Label(text="Résultat estimé = ventes − coût des produits vendus − dépenses.", size_hint_y=None, height=dp(32), font_size="11sp", color=(0.58,0.66,0.77,1), halign="left", valign="middle"))

        groupes = [
          ("PRODUITS & STOCK", [("＋ Ajouter un produit",self.ajouter_produit_gui),("Produits et stock",self.produits_stock_gui),("Alertes de stock",self.alertes_stock_gui),("Modifier / supprimer",self.gerer_produits_gui),("Entrée de stock",self.entree_stock_gui)]),
          ("VENTES & CLIENTS", [("CAISSE RAPIDE · panier + reçu",self.caisse_rapide_gui),("Vente au comptant",lambda *_:self.vente_gui(credit=False)),("Vente à crédit",lambda *_:self.vente_gui(credit=True)),("Historique des ventes",self.historique_ventes_gui),("Ajouter un client",self.ajouter_client_gui),("Liste des clients",self.clients_gui),("Dettes des clients",self.dettes_gui),("Remboursements",self.remboursement_gui),("Ajouter un fournisseur",self.ajouter_fournisseur_gui),("Liste des fournisseurs",self.fournisseurs_gui)]),
          ("DÉPENSES & RAPPORTS", [("Enregistrer une dépense",self.depense_gui),("Bilan du jour",self.bilan_jour_gui),("Bilan du mois",self.bilan_mensuel_gui),("Graphiques",self.graphiques_gui),("Exporter rapports CSV",self.exporter_rapports_gui)]),
          ("SAUVEGARDE & SÉCURITÉ", [("Sauvegarder les données",self.sauvegarder_gui),("Restaurer une sauvegarde",self.restaurer_base_gui),("Sauvegarde rapide",self.sauvegarder_base_gui)])]
        boutons_par_groupe = {}
        for titre, elements in groupes:
            contenu.add_widget(Label(text=f"[b]{titre}[/b]", markup=True, size_hint_y=None, height=dp(32), font_size="14sp", color=(0.35,0.91,0.68,1), halign="left", valign="middle"))
            g = GridLayout(cols=2, spacing=dp(8), size_hint_y=None, row_default_height=dp(62), row_force_default=True)
            g.bind(minimum_height=g.setter("height"))
            for nom, fn in elements:
                b=Button(text=nom, font_size="12sp", bold=True, color=(0.91,0.95,1,1), background_normal="", background_down="", background_color=(0.075,0.105,0.18,1))
                with b.canvas.before:
                    Color(0.12,0.19,0.28,1); shape=RoundedRectangle(pos=b.pos,size=b.size,radius=[dp(12)])
                b.bind(pos=lambda o,v,sh=shape:setattr(sh,"pos",v), size=lambda o,v,sh=shape:setattr(sh,"size",v))
                b.bind(on_release=fn); g.add_widget(b)
            contenu.add_widget(g); boutons_par_groupe[titre]=elements
        contenu.add_widget(Label(text="BoutiqueFacile · hors connexion · vos données restent sur votre téléphone", size_hint_y=None, height=dp(38), font_size="10sp", color=(0.48,0.56,0.68,1)))
        zone.add_widget(contenu); page.add_widget(zone)

        nav = BoxLayout(orientation="horizontal", size_hint_y=None, height=dp(62), spacing=dp(4), padding=[dp(6),dp(5)])
        with nav.canvas.before:
            Color(0.055,0.075,0.13,1); nf=RoundedRectangle(pos=nav.pos,size=nav.size,radius=[dp(14)])
        nav.bind(pos=lambda o,v:setattr(nf,"pos",v), size=lambda o,v:setattr(nf,"size",v))
        actions = [("⌂  Accueil",lambda *_:zone.scroll_to(entete, padding=dp(8)),(0.07,0.15,0.23,1)),
                   ("▦  Stock",self.produits_stock_gui,(0.05,0.55,0.39,1)),
                   ("▣  Ventes",self.caisse_rapide_gui,(0.05,0.55,0.39,1)),
                   ("•••  Plus",self.bilan_jour_gui,(0.07,0.15,0.23,1))]
        for nom, fn, col in actions:
            b=Button(text=nom, font_size="11sp", bold=True, color=(0.94,0.98,1,1), background_normal="", background_color=col)
            b.bind(on_release=fn); nav.add_widget(b)
        page.add_widget(nav)
        return page

    def build(self):
        self.title = "BoutiqueFacile"
        # Redimensionne la fenêtre quand le clavier Android apparaît.
        Window.softinput_mode = "resize"
        Window.clearcolor = (0.035, 0.055, 0.10, 1)
        try:
            interface = self.construire_interface()
            # Actualisation automatique toutes les 3 secondes : les changements
            # enregistrés dans les formulaires apparaissent sans redémarrer.
            self._dashboard_clock = Clock.schedule_interval(
                self.actualiser_tableau_de_bord, 3
            )
            # Copie automatique au démarrage puis toutes les 6 heures.
            self.sauvegarde_automatique()
            self._backup_clock = Clock.schedule_interval(
                self.sauvegarde_automatique, AUTO_BACKUP_INTERVAL
            )
            # La fenêtre PIN bloque l’accès à l’interface jusqu’à validation.
            Clock.schedule_once(self.demander_pin, 0.4)
            return interface
        except Exception as exc:
            self.afficher_message("Erreur de démarrage", exc)
            return Label(text="Erreur au démarrage. Consulte le message.")

    def on_stop(self):
        if self._dashboard_clock is not None:
            self._dashboard_clock.cancel()
            self._dashboard_clock = None
        if self._backup_clock is not None:
            self._backup_clock.cancel()
            self._backup_clock = None


if __name__ == "__main__":
    BoutiqueFacileApp().run()
