# Deep-Dive Analysis: Stored Procedures

**Project:** sql und showcase

**Analysis Date:** 2026-06-30

---

## Executive Summary

- **Total Procedures Analyzed:** 227

- **Skipped Due to Errors:** 0

- **Average Size:** 278 lines

- **Compliance-Relevant:** 92 (40.5%)

- **Accesses Sensitive Data:** 202 (89.0%)

- **Uses Cursors:** 109 (48.0%)

- **Uses Dynamic SQL:** 170 (74.9%)

- **Performs DML:** 0 (0.0%)

- **Accesses Remote Data:** 210 (92.5%)

---

## Criteria Distribution

- **REMOTE_DATA:** 210 (92.5%)

- **SENSITIVE_DATA:** 202 (89.0%)

- **PERFORMS_DML:** 201 (88.5%)

- **DYNAMIC_SQL:** 170 (74.9%)

- **USES_CURSOR:** 109 (48.0%)

- **COMPLIANCE:** 92 (40.5%)

---

## Top 20 Largest Procedures

| # | Name | Size (lines) | Author | Department | Compliance |

|---|------|-------------|--------|------------|------------|

|1|**sp_Create_Best_Execution**|1435|Bernhard Hofwimmer|Compliance, Brokerage, ProductGovernance|✅|

|2|**sp_Create_SRD_2_Interface**|1019|Bernhard Hofwimmer|BackOffice|❌|

|3|**sp_EvidenzVerwaltung**|1008|Bernhard Hofwimmer|Private Banking, Compliance, Business Services|✅|

|4|**sp_Create_CRS_Review**|861|Bernhard Hofwimmer|Core Banking|✅|

|5|**sp_Write_Kunden_Salden**|854|Bernhard Hofwimmer|Controlling|❌|

|6|**sp_Create_Kest_Befreiung_Test**|833|Bernhard Hofwimmer|Business Services, CoreBanking|✅|

|7|**sp_Create_Kest_Befreiung**|830|Bernhard Hofwimmer|Business Services, CoreBanking|✅|

|8|**sp_Create_FinMgr_MasterDaten**|806|Authority			= @Authority,|Core Banking|✅|

|9|**sp_Create_WP_Trans_Historie**|781|Bernhard Hofwimmer|Institutional PB|❌|

|10|**sp_ZVK_Taeglich**|771|Bernhard Hofwimmer|Back Office, Private Banking|✅|

|11|**sp_Create_High_Volume_Kunden**|737|Bernhard Hofwimmer|Core Banking|✅|

|12|**sp_Ueberziehungen**|729|Bernhard Hofwimmer|Kreditmanagement|✅|

|13|**sp_Ueberziehungen_Test**|694|Bernhard Hofwimmer|Kreditmanagement|✅|

|14|**sp_sperrquittierungen**|688|Bernhard Hofwimmer|Private Banking, Compliance|✅|

|15|**sp_RiskScoring_Kontrolle**|668|Bernhard Hofwimmer|Compliance|✅|

|16|**sp_Create_High_Volume_Kunden**|660|Bernhard Hofwimmer|Core Banking|✅|

|17|**sp_ZVK_RU_BY_UA**|647|Bernhard Hofwimmer|Compliance|✅|

|18|**sp_Create_FinMgr_Bewegungen**|622|Bernhard Hofwimmer|Gesamt Bank|❌|

|19|**sp_Read_Tambas_Daten_FinMgr**|621|Bernhard Hofwimmer|Core Banking|❌|

|20|**sp_Create_Evidenzen_OnBase**|615|Bernhard Hofwimmer|Compliance|✅|

---

## Compliance-Relevant Procedures (Top 30)

| # | Name | Size | Purpose |

|---|------|------|---------|

|1|**sp_Create_Best_Execution**|1435|** Funktion		: Daten zur berprfung Best Execution erstellen|

|2|**sp_EvidenzVerwaltung**|1008|** Funktion		: Erzeugen und versenden Liste der  flligen/fll|

|3|**sp_Create_CRS_Review**|861|** Funktion		: Erzeugen der Basisdaten fr CRS Listen aus Tam|

|4|**sp_Create_Kest_Befreiung_Test**|833|** Funktion		: Daten fr die elektronische Kest Befreiungserk|

|5|**sp_Create_Kest_Befreiung**|830|** Funktion		: Daten fr die elektronische Kest Befreiungserk|

|6|**sp_Create_FinMgr_MasterDaten**|806|** Funktion		: STammdaten fr FinanceManager aus Tambass lese|

|7|**sp_ZVK_Taeglich**|771|Verwendungszweck|

|8|**sp_Create_High_Volume_Kunden**|737|** Funktion		: Kontrolliste Dokumente/Evidenzen fr High Valu|

|9|**sp_Ueberziehungen**|729|** Funktion		: Erzeugen und versenden Liste aller Kontoberzi|

|10|**sp_Ueberziehungen_Test**|694|** Funktion		: Erzeugen und versenden Liste aller Kontoberzi|

|11|**sp_sperrquittierungen**|688|ZVK_Verwendung	= LEFT(RTRIM(a.Zweck1), 35) + LEFT(RTRIM(a.Zw|

|12|**sp_RiskScoring_Kontrolle**|668|** Funktion		: Erzeugen und versenden der RiskScoring Kontro|

|13|**sp_Create_High_Volume_Kunden**|660|** Funktion		: Kontrolliste Dokumente/Evidenzen fr High Valu|

|14|**sp_ZVK_RU_BY_UA**|647|Verwendungszweck,|

|15|**sp_Create_Evidenzen_OnBase**|615|** Funktion		: Erzeugen und aktualisiern der Tabelle Evidenz|

|16|**sp_Create_goAML_Transactions**|569|transaction_description		= ZWECK1 + ' ' + ZWECK2 + ' ' + ZWE|

|17|**sp_Read_Impairment_Daten**|511|** Funktion		: Roh-Daten  fr Impairment Test aus Tambas lese|

|18|**sp_WP_Orders**|498|** Funktion		: Erstellen und versenden der Liste der WP Orde|

|19|**sp_Kupon_QI_Abstimmung**|485|** Funktion		: Erstellen der QI Abstimmung|

|20|**sp_Create_ZVK_Eingang_OnBase**|482|Payment_Reason		= LEFT(RTRIM(a.Zweck1), 50) + LEFT(RTRIM(a.Z|

|21|**sp_InvestorProfile**|469|Zweck				= @Zweck,|

|22|**sp_ZVK_VS_Schwellenwert**|442|ZVKKBZW3		As Zweck3,|

|23|**sp_Check_Formular_Frequenz**|441|** Funktion		: Prfung Frequenz aller Belege (gesetzl. period|

|24|**sp_Create_Risk_Review_OnBase_Test**|429|** Funktion		: Erzeugen Tabelle aller bereits fällig geworde|

|25|**sp_Create_CRS_Listen**|412|** Funktion		: Erzeugen der Daten fr die Kontrollisten der C|

|26|**sp_Risk_OENB**|403|** Funktion		: Erzeugen Daten fr OENB Risk Prfung|

|27|**sp_GW_Auswertungen**|402|** Funktion		: Erstellen der Tabellen fr die Risikoanlayse v|

|28|**sp_sperraenderung**|397|** Funktion		: Erstellen und versenden der Liste aller nderu|

|29|**sp_Aktive_Sperren_KD_KK**|392|** Funktion		: Erstellen und versenden der Liste aller aktiv|

|30|**sp_Kunden_Cash_Only**|391|** Funktion		: Kontrolliste Kunden nur Cash|

---

## Procedures Using Dynamic SQL or Cursors

| Name | Size | Dynamic SQL | Cursor | DML | Remote |

|------|------|-------------|--------|-----|--------|

|**sp_Create_Best_Execution**|1435|✅|✅|❌|✅|

|**sp_Create_SRD_2_Interface**|1019|✅|✅|❌|✅|

|**sp_EvidenzVerwaltung**|1008|✅|✅|❌|✅|

|**sp_Create_CRS_Review**|861|✅|✅|❌|✅|

|**sp_Write_Kunden_Salden**|854|❌|✅|❌|✅|

|**sp_Create_Kest_Befreiung_Test**|833|✅|✅|❌|✅|

|**sp_Create_Kest_Befreiung**|830|✅|✅|❌|✅|

|**sp_Create_FinMgr_MasterDaten**|806|❌|✅|❌|❌|

|**sp_Create_WP_Trans_Historie**|781|✅|✅|❌|✅|

|**sp_ZVK_Taeglich**|771|✅|✅|❌|✅|

|**sp_Create_High_Volume_Kunden**|737|✅|✅|❌|✅|

|**sp_Ueberziehungen**|729|✅|✅|❌|✅|

|**sp_Ueberziehungen_Test**|694|✅|✅|❌|✅|

|**sp_sperrquittierungen**|688|✅|✅|❌|✅|

|**sp_RiskScoring_Kontrolle**|668|❌|✅|❌|✅|

|**sp_Create_High_Volume_Kunden**|660|✅|✅|❌|✅|

|**sp_ZVK_RU_BY_UA**|647|✅|✅|❌|✅|

|**sp_Create_FinMgr_Bewegungen**|622|✅|✅|❌|✅|

|**sp_Read_Tambas_Daten_FinMgr**|621|✅|❌|❌|✅|

|**sp_Create_Evidenzen_OnBase**|615|✅|✅|❌|✅|

|**sp_Create_goAML_Transactions**|569|✅|✅|❌|✅|

|**sp_Kreditkarten_Monatlich**|520|❌|✅|❌|✅|

|**sp_ATI_Investments**|519|✅|✅|❌|✅|

|**sp_Read_Impairment_Daten**|511|✅|❌|❌|✅|

|**sp_Create_Raquest_Analyse**|510|✅|✅|❌|✅|

|**sp_Write_Kunden_MonatsendDaten**|509|✅|✅|❌|✅|

|**sp_WP_Orders**|498|✅|✅|❌|✅|

|**sp_Bankbuch_Depotbestand**|496|✅|❌|❌|✅|

|**sp_Kupon_QI_Abstimmung**|485|✅|✅|❌|✅|

|**sp_Create_ZVK_Eingang_OnBase**|482|✅|✅|❌|✅|

|**sp_Bodensatz**|470|❌|✅|❌|✅|

|**sp_InvestorProfile**|469|❌|✅|❌|✅|

|**sp_Create_High_Watermarks**|456|✅|✅|❌|✅|

|**sp_ZVK_VS_Schwellenwert**|442|✅|✅|❌|✅|

|**sp_Check_Formular_Frequenz**|441|✅|✅|❌|✅|

|**sp_Create_Risk_Review_OnBase_Test**|429|✅|✅|❌|✅|

|**sp_Create_SupportNet_offen**|420|❌|✅|❌|❌|

|**sp_Gold_Kontrakte**|418|✅|❌|❌|✅|

|**sp_Create_CRS_Listen**|412|❌|✅|❌|❌|

|**sp_Risk_OENB**|403|✅|❌|❌|✅|

|**sp_sperraenderung**|397|✅|✅|❌|✅|

|**sp_Aktive_Sperren_KD_KK**|392|✅|✅|❌|✅|

|**sp_Dauerauftraege_Privat**|391|✅|✅|❌|✅|

|**sp_Kunden_Cash_Only**|391|✅|✅|❌|✅|

|**sp_Kunden_Hochrisiko**|381|✅|✅|❌|✅|

|**sp_Create_ENR_Balances**|367|✅|✅|❌|✅|

|**sp_OTC_Dokumente**|365|❌|✅|❌|✅|

|**sp_RiskScoring_Onbase**|362|✅|✅|❌|✅|

|**sp_WP_Trans_Check**|356|✅|✅|❌|✅|

|**sp_ZVK_Kontrakte**|350|✅|✅|❌|✅|

|**sp_Mailing_Kunden**|346|✅|✅|❌|✅|

|**sp_Check_Smart_Invest**|342|✅|✅|❌|✅|

|**sp_Mailing_Gruppen_Kunden**|337|✅|✅|❌|✅|

|**sp_Risk_Review_Check**|334|✅|❌|❌|✅|

|**sp_ZVK_Eingang_Check24**|333|✅|✅|❌|✅|

|**sp_Check_Kunden_ohne**|332|✅|❌|❌|✅|

|**sp_ZVK_Eingang_Check24_Test**|332|✅|✅|❌|✅|

|**sp_Risikoklasse_Durchschnitt_VV**|328|❌|✅|❌|✅|

|**sp_Risk_Review_OnBase_Details**|325|✅|✅|❌|✅|

|**sp_Create_Impairment_Test**|325|❌|✅|❌|❌|

|**sp_ZVK_Compliance**|321|✅|❌|❌|✅|

|**sp_Create_Kredit_Evidenzen_OnBase**|320|✅|✅|❌|✅|

|**sp_Create_AML_Art5**|319|✅|✅|❌|✅|

|**sp_Offene_Orders**|317|✅|✅|❌|✅|

|**sp_Write_Treasury_Salden**|313|✅|❌|❌|✅|

|**sp_Kunden_Fluktuation**|312|✅|✅|❌|✅|

|**sp_Send_SRD_2_CSV**|310|✅|✅|❌|✅|

|**sp_Bar_Transaktionen**|308|✅|✅|❌|✅|

|**sp_Ablaufende_Anleihen**|306|✅|✅|❌|✅|

|**sp_Check_TIN_Gueltigkeit**|305|✅|✅|❌|✅|

|**sp_Kundenprofil_Depotbestand**|293|✅|❌|❌|✅|

|**sp_Create_Evidenzen_OnBase**|293|✅|✅|❌|✅|

|**sp_Check_Formular_Frequenz**|288|✅|✅|❌|✅|

|**sp_FATCA_IA_Faellig**|282|✅|✅|❌|✅|

|**sp_VV_IP_AenderungAnlage**|282|✅|❌|❌|✅|

|**sp_check_Depots_Bestand**|277|❌|✅|❌|✅|

|**sp_Write_Kunden_Postfach**|276|❌|✅|❌|✅|

|**sp_Create_DatenExport_UniCredit**|273|❌|✅|❌|✅|

|**sp_FX_Forwards**|272|✅|✅|❌|✅|

|**sp_Kunden_Check_Compliance**|269|✅|❌|❌|✅|

|**sp_Kurscheck_Nostro_Bestand**|269|✅|✅|❌|✅|

|**sp_ESG_Check**|264|✅|✅|❌|✅|

|**sp_Create_ZVK_Master_Data**|264|✅|❌|❌|✅|

|**sp_Check_Kunden_mit**|259|✅|❌|❌|✅|

|**sp_Check_Konten_ohne**|249|✅|❌|❌|✅|

|**sp_Check_WP_Art_vs_Depot**|249|❌|✅|❌|✅|

|**sp_Create_Treasury_Listen**|247|✅|❌|❌|✅|

|**sp_Abgelaufene_Vollmachten**|246|✅|❌|❌|✅|

|**sp_Create_Onbase_Master_Data**|246|✅|❌|❌|✅|

|**sp_Kredit_Unterschreitungen**|245|✅|✅|❌|✅|

|**sp_Create_TCM_Check_OnBase**|241|✅|✅|❌|✅|

|**sp_Create_ATIExport_UniCredit**|237|✅|✅|❌|✅|

|**sp_WP_Bewegungen**|237|✅|❌|❌|✅|

|**sp_Create_Manual_Risk_Review_Test**|236|✅|❌|❌|✅|

|**sp_Abgelaufene_US_Dokumente**|235|✅|❌|❌|✅|

|**sp_ZVK_Kontrakte_Ford_Verb**|235|✅|❌|❌|✅|

|**sp_Kunden_Loeschung_DSGVO**|234|✅|❌|❌|✅|

|**sp_Check_Relevante_Person**|233|✅|✅|❌|✅|

|**sp_Check_REPP_vs_REKS**|233|✅|✅|❌|✅|

|**sp_MIFID_II_BestEx_Offenlegung**|228|✅|❌|❌|✅|

|**sp_Dokumente**|227|❌|✅|❌|✅|

|**sp_Create_Kupon_Tilgung**|227|❌|✅|❌|✅|

|**sp_AML_Meldung**|226|✅|✅|❌|✅|

|**sp_CRS_FATCA_Listen**|225|✅|❌|❌|✅|

|**sp_sperrquittierungen_Quartal**|224|✅|✅|❌|✅|

|**sp_Check_Konten_Zinsgruppe**|222|✅|❌|❌|✅|

|**sp_Create_WPB_TCM_Clients**|222|❌|✅|❌|❌|

|**sp_Firmen_Ablaufende_Vollmachten**|220|✅|❌|❌|✅|

|**sp_Create_QI_UM_Daten**|212|✅|❌|❌|✅|

|**sp_Risk_Review_Abgeschlossen**|211|✅|❌|❌|✅|

|**sp_Check_Risk_Scoring**|210|✅|✅|❌|✅|

|**sp_Geburtstagskinder**|210|✅|✅|❌|✅|

|**sp_Review_Nostro_Bestsand_Risk**|207|✅|❌|❌|✅|

|**sp_Salden_KO_Sperre_CS**|204|✅|❌|❌|✅|

|**sp_Evidenzen_US_Dokumente**|200|✅|❌|❌|✅|

|**sp_Risk_Review_OnBase_Transaktionen**|197|✅|✅|❌|✅|

|**sp_Vollmachten_PEP**|197|✅|❌|❌|✅|

|**sp_Professionelle_Kunden**|196|❌|✅|❌|✅|

|**sp_Kunden_Risikoaenderung**|194|✅|✅|❌|✅|

|**sp_Forwards_Mature**|193|✅|✅|❌|✅|

|**sp_Create_Check24_OnBase**|192|✅|❌|❌|✅|

|**sp_Options**|191|❌|✅|❌|✅|

|**sp_OENB_MELDUNG_RU_BY**|189|✅|✅|❌|✅|

|**sp_Firmen_Fehlende_Vollmachten**|189|✅|❌|❌|✅|

|**sp_Kunden_Fluktuation_AdHoc**|188|✅|✅|❌|✅|

|**sp_FX_Kurse_Taeglich**|187|✅|❌|❌|✅|

|**sp_Check_MIFIR_Transaktionen**|187|✅|❌|❌|✅|

|**sp_ZVK_Ausgaenge_OnBase**|187|✅|❌|❌|✅|

|**sp_Create_KPMG_Datenabzug**|186|✅|❌|❌|✅|

|**sp_Create_Check24_OnBase_Test**|185|✅|❌|❌|✅|

|**sp_Neue_Wertpapiere**|184|✅|✅|❌|✅|

|**sp_Orders_via_Navigator**|184|✅|❌|❌|✅|

|**sp_Create_CRS_Meldung_TPAM**|184|✅|✅|❌|✅|

|**sp_Fehlerhafte_Quartalsspesen**|182|✅|❌|❌|✅|

|**sp_Check_Vermittlerdaten_Controlling**|182|✅|❌|❌|✅|

|**sp_Ablaufende_Festgelder**|181|✅|✅|❌|✅|

|**sp_Check_Kunden_Eroeffnungsdatum**|179|✅|❌|❌|✅|

|**sp_Check_KAMA_Lieferungen**|174|✅|❌|❌|✅|

|**sp_Kontoregister_Kontrolle**|173|❌|✅|❌|❌|

|**sp_Create_Risk_Review_OnBase**|171|✅|✅|❌|✅|

|**sp_Create_High_Watermarks_YtD**|171|✅|❌|❌|✅|

|**sp_Check_KundenProfil**|169|✅|❌|❌|✅|

|**sp_Check_Kupon_Kest**|168|✅|❌|❌|✅|

|**sp_VV_Depot_Check**|166|✅|❌|❌|✅|

|**sp_Create_Konto_saldo**|164|✅|✅|❌|✅|

|**sp_Check_Kupon_Kest_Onbase**|163|✅|❌|❌|✅|

|**sp_Gold_Sparplaene**|161|✅|❌|❌|✅|

|**sp_Firmen_Vollmachten**|160|✅|❌|❌|✅|

|**sp_Check_SFTR_Valuation**|160|✅|❌|❌|✅|

|**sp_Manuelle_WP_Kurse**|160|✅|❌|❌|✅|

|**sp_Buchung_GuV_Konto**|158|✅|❌|❌|✅|

|**sp_Fehlerhafte_Corporate_Actions**|158|✅|❌|❌|✅|

|**sp_BO_Aenderungen**|157|✅|❌|❌|✅|

|**sp_Devisenhandel_Vontobel**|156|✅|❌|❌|✅|

|**sp_MIFIR_Transaktionen_Onbase**|156|✅|❌|❌|✅|

|**sp_findtext_SP**|156|❌|✅|❌|❌|

|**sp_Create_Verlustschwellenreport_Meldung**|155|✅|❌|❌|✅|

|**sp_findtext**|154|❌|✅|❌|❌|

|**sp_Create_WHVP_Trades**|154|✅|❌|❌|✅|

|**sp_Neue_WPs_Ohne_ISIN**|153|✅|❌|❌|✅|

|**sp_NeuKunden_Sutor**|152|✅|❌|❌|✅|

|**sp_VST_9999800011_Gegenbuchung**|147|✅|❌|❌|✅|

|**sp_SupportNet_vs_YouTrack**|145|❌|✅|❌|❌|

|**sp_Check_Portfolio_Reports**|145|✅|❌|❌|✅|

|**sp_WP_Kontrakte_Taeglich**|145|✅|❌|❌|✅|

|**sp_Firmen_ohne_BO**|140|✅|❌|❌|✅|

|**sp_Check_Doppelte_Kest_Tilgung**|137|✅|❌|❌|✅|

|**sp_Create_Benutzergruppen_Menuepunkte**|135|✅|✅|❌|✅|

|**sp_Create_Check24_Inaktiv_OnBase**|135|✅|✅|❌|✅|

|**sp_Create_Check24_Inaktiv_OnBase**|134|✅|✅|❌|✅|

|**sp_Create_Table_LaenderStamm**|133|✅|❌|❌|✅|

|**sp_Create_SRD_2_WP_Trans**|129|✅|❌|❌|✅|

|**sp_Create_WHVP_Balances**|129|✅|❌|❌|✅|

|**sp_Check_Kunden_Team_vs_CRM**|127|✅|❌|❌|✅|

|**sp_Create_Tambas_Assetera_Mapping**|127|✅|❌|❌|✅|

|**sp_Check24_Antrag_Inaktivieren**|125|✅|❌|❌|✅|

|**sp_Check_Kreditkonten_neu**|125|✅|❌|❌|✅|

|**sp_Check24_Antrag_Inaktivieren_Test**|125|✅|❌|❌|✅|

|**sp_Write_Kundenstamm_Controlling**|124|❌|✅|❌|✅|

|**sp_Create_Ablaufende_Garantien**|123|✅|❌|❌|✅|

|**sp_Risk_Review_Offen**|122|✅|❌|❌|✅|

|**sp_Check_PTP_W10**|121|✅|❌|❌|✅|

|**sp_Check_Mehrfache_Tin**|119|❌|✅|❌|❌|

|**sp_Konto_Abgleich_Valantic**|118|✅|❌|❌|✅|

|**sp_Quest_Auswertung**|115|✅|✅|❌|✅|

|**sp_Closed_clients_LMonth**|115|✅|✅|❌|✅|

|**sp_Check_CRS_Kontoregister**|113|❌|✅|❌|❌|

|**sp_Check_Jobs**|107|✅|❌|❌|❌|

|**sp_email_neu_angelegte_anleihen**|106|✅|❌|❌|✅|

|**sp_ZVK_Sepa_Ausgaenge_OnBase**|105|✅|❌|❌|✅|

|**sp_Geldhandel_Check24_OnBase**|102|✅|❌|❌|✅|

|**sp_FX_Kurse_His**|100|✅|❌|❌|✅|

|**sp_Geldhandel_Check24_OnBase_Test**|100|✅|❌|❌|✅|

|**sp_Create_ZVK_Valuta_OnBase**|98|✅|❌|❌|✅|

|**sp_ATI_Korrektur**|75|❌|✅|❌|❌|

|**sp_Write_Kunden_Sprache**|68|✅|✅|❌|✅|

|**sp_email_wertpapiere_umbenennen_eng**|67|✅|❌|❌|✅|

|**sp_Konten_Gueltigkeit**|54|✅|❌|❌|✅|

---

## Risk Assessment Summary

- **High Complexity (>5000 lines):** Several procedures exceed this threshold, indicating high maintenance cost and risk.

- **Dynamic SQL:** Increases SQL injection risk and reduces maintainability.

- **Cursors:** Can lead to performance issues and deadlocks.

- **DML Operations:** Direct data modification, high impact if incorrect.

- **Remote Data Access:** Linked servers increase latency and failure points.

- **Compliance:** 33 procedures are directly tied to regulatory reporting, critical for audits.

---

## Recommendations

### Immediate Actions

- **Review high-complexity procedures (>5000 lines):** Refactor, split, or document thoroughly.

- **Audit procedures using dynamic SQL:** Ensure inputs are sanitized to prevent SQL injection.

- **Optimize cursor usage:** Replace cursors with set-based operations where possible.

- **Document all compliance procedures:** Ensure clear ownership, purpose, and change control.

- **Review remote data access:** Monitor performance and failure rates for linked server calls.

- **Standardize metadata:** Enforce header comments for author, date, purpose, department in all new procedures.


### Long-Term Improvements

- **Implement a stored procedure inventory:** Centralize metadata for easier discovery and impact analysis.

- **Introduce code review gates:** Require peer review for procedures touching sensitive data or performing DML.

- **Automated testing:** Build a regression test suite for critical procedures.

- **Performance monitoring:** Log and alert on long-running procedures or frequent failures.

---

## All Procedures (Full List)

| # | Name | Size | Author | Department | Compliance | Sensitive Data | Dynamic SQL | Cursor | DML | Remote |

|---|------|------|--------|------------|------------|----------------|-------------|--------|-----|--------|

|1|**SP_Check_Depot_Spesen_Konto**|203|Bernhard Hofwimmer|Back Office und Business Services|❌|✅|❌|❌|❌|✅|

|2|**SP_Check_Spesen_Konto**|232|Bernhard Hofwimmer|Business Services|❌|✅|❌|❌|❌|✅|

|3|**SP_ErinnerungsMail_Nachbuchen_erlaubt**|64|Bernhard Hofwimmer|Rechnungswesen/Controlling|❌|❌|❌|❌|❌|❌|

|4|**sp_AML_Meldung**|226|Bernhard Hofwimmer|Compliance|✅|✅|✅|✅|❌|✅|

|5|**sp_ATI_Investments**|519|Bernhard Hofwimmer|ATI (G.Sutrich, R.Radeschnig)|❌|✅|✅|✅|❌|✅|

|6|**sp_ATI_Korrektur**|75|Bernhard Hofwimmer|ATI (G.Sutrich, R.Radeschnig)|❌|❌|❌|✅|❌|❌|

|7|**sp_Abgelaufene_US_Dokumente**|235|Bernhard Hofwimmer|Business Services|❌|✅|✅|❌|❌|✅|

|8|**sp_Abgelaufene_Vollmachten**|246|Bernhard Hofwimmer (nderung: Florian Wugeditsch)|Business Services|❌|✅|✅|❌|❌|✅|

|9|**sp_Ablaufende_Anleihen**|306|Bernhard Hofwimmer|Private Banking|❌|✅|✅|✅|❌|✅|

|10|**sp_Ablaufende_Festgelder**|181|Bernhard Hofwimmer|Private Banking|❌|✅|✅|✅|❌|✅|

|11|**sp_Aktive_Sperren_KD_KK**|392|Bernhard Hofwimmer|VARCHAR(3),|✅|✅|✅|✅|❌|✅|

|12|**sp_BEPRO_Kondition**|42|N/A|N/A|❌|❌|❌|❌|❌|✅|

|13|**sp_BO_Aenderungen**|157|Bernhard Hofwimmer|Business Services|❌|✅|✅|❌|❌|✅|

|14|**sp_Bankbuch_Depotbestand**|496|Bernhard Hofwimmer|Rechnungswesen|❌|✅|✅|❌|❌|✅|

|15|**sp_Bar_Transaktionen**|308|Bernhard Hofwimmer|Privat Banking|✅|✅|✅|✅|❌|✅|

|16|**sp_Bodensatz**|470|Bernhard Hofwimmer|Krediverwaltung|❌|✅|❌|✅|❌|✅|

|17|**sp_Bodensatz_konten**|57|Bernhard Hofwimmer|Kreditverwaltung|❌|❌|❌|❌|❌|✅|

|18|**sp_Buchung_GuV_Konto**|158|Bernhard Hofwimmer|Buchhaltung|❌|✅|✅|❌|❌|✅|

|19|**sp_CRS_FATCA_Listen**|225|Bernhard Hofwimmer|Business Services|✅|✅|✅|❌|❌|✅|

|20|**sp_Check24_Antrag_Inaktivieren**|125|Bernhard Hofwimmer|BackOffice|❌|✅|✅|❌|❌|✅|

|21|**sp_Check24_Antrag_Inaktivieren_Test**|125|Bernhard Hofwimmer|BackOffice|❌|✅|✅|❌|❌|✅|

|22|**sp_Check_603_vs_601**|123|Bernhard Hofwimmer|Business Services|✅|✅|❌|❌|❌|✅|

|23|**sp_Check_Benchmark_VV_Kunden**|113|Bernhard Hofwimmer|Business Services|❌|✅|❌|❌|❌|✅|

|24|**sp_Check_CRS_Kontoregister**|113|Bernhard Hofwimmer|CoreBanking/BackOffice|✅|✅|❌|✅|❌|❌|

|25|**sp_Check_Doppelte_Kest_Tilgung**|137|Bernhard Hofwimmer|BO/Settlement|❌|❌|✅|❌|❌|✅|

|26|**sp_Check_Eigenbestand_Lagerstelle**|143|Bernhard Hofwimmer|Back Office|❌|✅|❌|❌|❌|✅|

|27|**sp_Check_Formular_Frequenz**|288|Michaela Richtsfeld|Business Service|✅|✅|✅|✅|❌|✅|

|28|**sp_Check_Formular_Frequenz**|441|Florian Wugeditsch)|Business Service|✅|✅|✅|✅|❌|✅|

|29|**sp_Check_Jobs**|107|Bernhard Hofwimmer|Core Banking|❌|❌|✅|❌|❌|❌|

|30|**sp_Check_KAMA_Lieferungen**|174|Florian Wugeditsch|Settlement|❌|❌|✅|❌|❌|✅|

|31|**sp_Check_Konten_Zinsgruppe**|222|Bernhard Hofwimmer|Business Services|❌|✅|✅|❌|❌|✅|

|32|**sp_Check_Konten_ohne**|249|Michaela Richtsfeld|Business Service|✅|✅|✅|❌|❌|✅|

|33|**sp_Check_Kreditkonten_neu**|125|Bernhard Hofwimmer|Business Services|✅|✅|✅|❌|❌|✅|

|34|**sp_Check_KundenProfil**|169|Bernhard Hofwimmer|Business Services|❌|✅|✅|❌|❌|✅|

|35|**sp_Check_Kunden_Eroeffnungsdatum**|179|Bernhard Hofwimmer|Business Services|❌|✅|✅|❌|❌|✅|

|36|**sp_Check_Kunden_Team_vs_CRM**|127|Bernhard Hofwimmer|Business Services|❌|✅|✅|❌|❌|✅|

|37|**sp_Check_Kunden_mit**|259|Michaela Richtsfeld|Business Service|✅|✅|✅|❌|❌|✅|

|38|**sp_Check_Kunden_ohne**|332|Michaela Richtsfeld|Business Service|✅|✅|✅|❌|❌|✅|

|39|**sp_Check_Kupon_Kest**|168|Bernhard Hofwimmer|BO/Settlement|❌|✅|✅|❌|❌|✅|

|40|**sp_Check_Kupon_Kest_Onbase**|163|Florian Wugeditsch|Settlement|✅|✅|✅|❌|❌|✅|

|41|**sp_Check_LEI_Gueltigkeit**|131|Bernhard Hofwimmer|Business Services|❌|✅|❌|❌|❌|✅|

|42|**sp_Check_MIFIR_Transaktionen**|187|Bernhard Hofwimmer|Settlement|✅|✅|✅|❌|❌|✅|

|43|**sp_Check_Mehrfache_Tin**|119|Bernhard Hofwimmer|Core Banking|❌|✅|❌|✅|❌|❌|

|44|**sp_Check_PTP_W10**|121|Bernhard Hofwimmer|Product Governance|❌|❌|✅|❌|❌|✅|

|45|**sp_Check_Portfolio_Reports**|145|Bernhard Hofwimmer|Business Services|✅|✅|✅|❌|❌|✅|

|46|**sp_Check_Quartalsspesen**|85|Bernhard Hofwimmer|Back Office, Business Services|❌|✅|❌|❌|❌|❌|

|47|**sp_Check_REPP_vs_REKS**|233|Bernhard Hofwimmer|Core Banking|❌|✅|✅|✅|❌|✅|

|48|**sp_Check_Relevante_Person**|233|Bernhard Hofwimmer|Business Services|❌|✅|✅|✅|❌|✅|

|49|**sp_Check_Risk_Scoring**|210|Bernhard Hofwimmer|Compliance|✅|✅|✅|✅|❌|✅|

|50|**sp_Check_SFTR_Valuation**|160|Bernhard Hofwimmer|Back Office|❌|✅|✅|❌|❌|✅|

|51|**sp_Check_Smart_Invest**|342|Bernhard Hofwimmer|Private Banking|❌|✅|✅|✅|❌|✅|

|52|**sp_Check_TIN_Gueltigkeit**|305|Bernhard Hofwimmer|Core Banking|✅|✅|✅|✅|❌|✅|

|53|**sp_Check_VV_Tipas**|176|Bernhard Hofwimmer|Business Services|❌|✅|❌|❌|❌|✅|

|54|**sp_Check_Vermittlerdaten_Controlling**|182|Michaela Richtsfeld|Core Banking, Private Banking|❌|✅|✅|❌|❌|✅|

|55|**sp_Check_WP_Art_vs_Depot**|249|Bernhard Hofwimmer|BackOffice|❌|✅|❌|✅|❌|✅|

|56|**sp_Closed_clients_LMonth**|115|Bernhard Hofwimmer|Compliance|✅|✅|✅|✅|❌|✅|

|57|**sp_Create_AML_Art5**|319|Bernhard Hofwimmer|Compliance|✅|✅|✅|✅|❌|✅|

|58|**sp_Create_ATIExport_UniCredit**|237|Bernhard Hofwimmer|Back Office|❌|✅|✅|✅|❌|✅|

|59|**sp_Create_Ablaufende_Garantien**|123|Bernhard Hofwimmer|Kreditverwaltung|❌|✅|✅|❌|❌|✅|

|60|**sp_Create_Benutzergruppen_Menuepunkte**|135|Bernhard Hofwimmer|Core Banking|❌|✅|✅|✅|❌|✅|

|61|**sp_Create_Best_Execution**|1435|Bernhard Hofwimmer|Compliance, Brokerage, ProductGovernance|✅|✅|✅|✅|❌|✅|

|62|**sp_Create_CRS_Listen**|412|Bernhard Hofwimmer|Core Banking|✅|✅|❌|✅|❌|❌|

|63|**sp_Create_CRS_Meldung_TPAM**|184|Bernhard Hofwimmer|CoreBanking|✅|✅|✅|✅|❌|✅|

|64|**sp_Create_CRS_Review**|861|Bernhard Hofwimmer|Core Banking|✅|✅|✅|✅|❌|✅|

|65|**sp_Create_Check24_Inaktiv_OnBase**|134|Bernhard Hofwimmer|BackOffice|❌|✅|✅|✅|❌|✅|

|66|**sp_Create_Check24_Inaktiv_OnBase**|135|Bernhard Hofwimmer|BackOffice|❌|✅|✅|✅|❌|✅|

|67|**sp_Create_Check24_OnBase**|192|Bernhard Hofwimmer|Gesamtbank|✅|✅|✅|❌|❌|✅|

|68|**sp_Create_Check24_OnBase_Test**|185|Bernhard Hofwimmer|Gesamtbank|✅|✅|✅|❌|❌|✅|

|69|**sp_Create_DatenExport_UniCredit**|273|Bernhard Hofwimmer|Back Office|❌|✅|❌|✅|❌|✅|

|70|**sp_Create_ENR_Balances**|367|Bernhard Hofwimmer|Institutional PB|❌|✅|✅|✅|❌|✅|

|71|**sp_Create_ENR_Positions**|120|Bernhard Hofwimmer|Institutional PB|❌|✅|❌|❌|❌|✅|

|72|**sp_Create_Evidenzen_OnBase**|293|Bernhard Hofwimmer|Compliance|✅|✅|✅|✅|❌|✅|

|73|**sp_Create_Evidenzen_OnBase**|615|Bernhard Hofwimmer|Compliance|✅|✅|✅|✅|❌|✅|

|74|**sp_Create_FMG_Plus_Positions**|222|Bernhard Hofwimmer|Institutional PB|❌|✅|❌|❌|❌|✅|

|75|**sp_Create_FinMgr_Bewegungen**|622|Bernhard Hofwimmer|Gesamt Bank|❌|✅|✅|✅|❌|✅|

|76|**sp_Create_FinMgr_MasterDaten**|806|Authority			= @Authority,|Core Banking|✅|✅|❌|✅|❌|❌|

|77|**sp_Create_High_Volume_Kunden**|660|Bernhard Hofwimmer|Core Banking|✅|✅|✅|✅|❌|✅|

|78|**sp_Create_High_Volume_Kunden**|737|Bernhard Hofwimmer|Core Banking|✅|✅|✅|✅|❌|✅|

|79|**sp_Create_High_Watermarks**|456|Bernhard Hofwimmer|Core Banking|❌|✅|✅|✅|❌|✅|

|80|**sp_Create_High_Watermarks_YtD**|171|Bernhard Hofwimmer|Core Banking|✅|✅|✅|❌|❌|✅|

|81|**sp_Create_IOMA_Portfolio**|222|Bernhard Hofwimmer|Institutional PB|❌|✅|❌|❌|❌|✅|

|82|**sp_Create_Impairment_Test**|325|Bernhard Hofwimmer|Rechnungswesen / Risk Management|✅|✅|❌|✅|❌|❌|

|83|**sp_Create_KPMG_Datenabzug**|186|Bernhard Hofwimmer|CoreBanking/Rechnungswesen|❌|✅|✅|❌|❌|✅|

|84|**sp_Create_Kest_Befreiung**|830|Bernhard Hofwimmer|Business Services, CoreBanking|✅|✅|✅|✅|❌|✅|

|85|**sp_Create_Kest_Befreiung_Test**|833|Bernhard Hofwimmer|Business Services, CoreBanking|✅|✅|✅|✅|❌|✅|

|86|**sp_Create_Konto_saldo**|164|Bernhard Hofwimmer|Private banking|❌|✅|✅|✅|❌|✅|

|87|**sp_Create_Kredit_Evidenzen_OnBase**|320|Michaela Richtsfeld|-- gesamt an Kreditabteilung|❌|✅|✅|✅|❌|✅|

|88|**sp_Create_Kupon_Tilgung**|227|Bernhard Hofwimmer|Treasury|❌|✅|❌|✅|❌|✅|

|89|**sp_Create_Manual_Risk_Review_Test**|236|Florian Wugeditsch|Gesamtbank|✅|✅|✅|❌|❌|✅|

|90|**sp_Create_Onbase_Master_Data**|246|Bernhard Hofwimmer|BS|❌|✅|✅|❌|❌|✅|

|91|**sp_Create_Portfolio_Valuation**|232|Bernhard Hofwimmer|PB CEE (R. Cup)|❌|✅|❌|❌|❌|✅|

|92|**sp_Create_QI_UM_Daten**|212|Bernhard Hofwimmer|CoreBanking/Melderegime|✅|✅|✅|❌|❌|✅|

|93|**sp_Create_Raquest_Analyse**|510|Bernhard Hofwimmer|Gesamtbank|❌|✅|✅|✅|❌|✅|

|94|**sp_Create_Risk_Review_OnBase**|171|Bernhard Hofwimmer|Private banking|✅|✅|✅|✅|❌|✅|

|95|**sp_Create_Risk_Review_OnBase_Test**|429|Bernhard Hofwimmer (Erweiterung: Florian Wugeditsch)|Gesamtbank|✅|✅|✅|✅|❌|✅|

|96|**sp_Create_SRD_2_Interface**|1019|Bernhard Hofwimmer|BackOffice|❌|✅|✅|✅|❌|✅|

|97|**sp_Create_SRD_2_WP_Trans**|129|Bernhard Hofwimmer|Gesamtbank|❌|✅|✅|❌|❌|✅|

|98|**sp_Create_SupportNet_offen**|420|a.Autor,|Gesamtbank|❌|✅|❌|✅|❌|❌|

|99|**sp_Create_Swiss_Alpine_Balances**|200|Bernhard Hofwimmer|Institutional PB|❌|❌|❌|❌|❌|✅|

|100|**sp_Create_TCM_Check_OnBase**|241|Bernhard Hofwimmer|Compliance/PrivateBanking|✅|✅|✅|✅|❌|✅|

|101|**sp_Create_Table_LaenderStamm**|133|Bernhard Hofwimmer|Compliance/OnBase|✅|✅|✅|❌|❌|✅|

|102|**sp_Create_Tambas_Assetera_Mapping**|127|Bernhard Hofwimmer|Gesamtbank|❌|✅|✅|❌|❌|✅|

|103|**sp_Create_Treasury_Listen**|247|Bernhard Hofwimmer|Treasury|❌|✅|✅|❌|❌|✅|

|104|**sp_Create_Valorlife_Portfolio**|238|Bernhard Hofwimmer|Institutional PB|❌|✅|❌|❌|❌|✅|

|105|**sp_Create_Verlustschwellenreport_Meldung**|155|Florian Wugeditsch|PBI|❌|✅|✅|❌|❌|✅|

|106|**sp_Create_WHVP_Balances**|129|Bernhard Hofwimmer|Institutional PB|❌|❌|✅|❌|❌|✅|

|107|**sp_Create_WHVP_Trades**|154|N/A|N/A|❌|❌|✅|❌|❌|✅|

|108|**sp_Create_WPB_TCM_Clients**|222|Bernhard Hofwimmer|Compliance / Back Office|✅|✅|❌|✅|❌|❌|

|109|**sp_Create_WP_Trans_Historie**|781|Bernhard Hofwimmer|Institutional PB|❌|✅|✅|✅|❌|✅|

|110|**sp_Create_ZVK_Eingang_OnBase**|482|Bernhard Hofwimmer|= c.Abteilung,|✅|✅|✅|✅|❌|✅|

|111|**sp_Create_ZVK_Master_Data**|264|Bernhard Hofwimmer|is null|✅|✅|✅|❌|❌|✅|

|112|**sp_Create_ZVK_Valuta_OnBase**|98|Bernhard Hofwimmer|BO/Zahlungsverkehr|❌|❌|✅|❌|❌|✅|

|113|**sp_Create_goAML_Transactions**|569|Bernhard Hofwimmer|Compliance|✅|✅|✅|✅|❌|✅|

|114|**sp_Dauerauftraege_Privat**|391|Bernhard Hofwimmer|Back Office|❌|✅|✅|✅|❌|✅|

|115|**sp_Devisenhandel_Vontobel**|156|Bernhard Hofwimmer|Treasury|❌|✅|✅|❌|❌|✅|

|116|**sp_Dokumente**|227|Bernhard Hofwimmer|Private Banking|❌|✅|❌|✅|❌|✅|

|117|**sp_ESG_Check**|264|Bernhard Hofwimmer|Product Governance|❌|✅|✅|✅|❌|✅|

|118|**sp_EvidenzVerwaltung**|1008|Bernhard Hofwimmer|Private Banking, Compliance, Business Services|✅|✅|✅|✅|❌|✅|

|119|**sp_Evidenzen_US_Dokumente**|200|Bernhard Hofwimmer|Business Services|❌|✅|✅|❌|❌|✅|

|120|**sp_FATCA_IA_Faellig**|282|Bernhard Hofwimmer|Private Banking|✅|✅|✅|✅|❌|✅|

|121|**sp_FX_Forwards**|272|Bernhard Hofwimmer|Private Banking|❌|✅|✅|✅|❌|✅|

|122|**sp_FX_Kurse_His**|100|Bernhard Hofwimmer|Gesamtbank|❌|✅|✅|❌|❌|✅|

|123|**sp_FX_Kurse_Taeglich**|187|Bernhard Hofwimmer|Brokerage/Treasury (die 18 Hauptwhrungen)|❌|✅|✅|❌|❌|✅|

|124|**sp_Fatca_Relevanz**|150|Bernhard Hofwimmer|Business Services|✅|✅|❌|❌|❌|✅|

|125|**sp_Fehlerhafte_Corporate_Actions**|158|Bernhard Hofwimmer|Settlement|❌|✅|✅|❌|❌|✅|

|126|**sp_Fehlerhafte_Quartalsspesen**|182|Bernhard Hofwimmer|Core Banking|❌|✅|✅|❌|❌|✅|

|127|**sp_Firmen_Ablaufende_Vollmachten**|220|Florian Wugeditsch|Business Services|❌|✅|✅|❌|❌|✅|

|128|**sp_Firmen_Fehlende_Vollmachten**|189|Florian Wugeditsch|Business Services|❌|✅|✅|❌|❌|✅|

|129|**sp_Firmen_Vollmachten**|160|Bernhard Hofwimmer|Business Services|❌|✅|✅|❌|❌|✅|

|130|**sp_Firmen_ohne_BO**|140|Bernhard Hofwimmer|Core Banking|❌|✅|✅|❌|❌|✅|

|131|**sp_Formulare_Inaktivieren**|145|Bernhard Hofwimmer|Business Services|✅|✅|❌|❌|❌|✅|

|132|**sp_Forwards_Mature**|193|Bernhard Hofwimmer|Private Banking|❌|✅|✅|✅|❌|✅|

|133|**sp_GW_Auswertungen**|402|Bernhard Hofwimmer|Compliance|✅|✅|❌|❌|❌|✅|

|134|**sp_Geburtstagskinder**|210|Bernhard Hofwimmer|Privat Banking (PBA&I, CEE)|✅|✅|✅|✅|❌|✅|

|135|**sp_Geldhandel_Check24_OnBase**|102|Bernhard Hofwimmer|Gesamtbank|❌|✅|✅|❌|❌|✅|

|136|**sp_Geldhandel_Check24_OnBase_Test**|100|Bernhard Hofwimmer|Gesamtbank|❌|✅|✅|❌|❌|✅|

|137|**sp_Gold_Kontrakte**|418|Bernhard Hofwimmer|Back Office|❌|✅|✅|❌|❌|✅|

|138|**sp_Gold_Sparplaene**|161|Bernhard Hofwimmer|Back Office|❌|✅|✅|❌|❌|✅|

|139|**sp_InvestorProfile**|469|Bernhard Hofwimmer|Privat Banking, Compliance|✅|✅|❌|✅|❌|✅|

|140|**sp_Konten_Gueltigkeit**|54|Florian Wugeditsch, 25.06.2026|N/A|❌|✅|✅|❌|❌|✅|

|141|**sp_Konto_Abgleich_Valantic**|118|Bernhard Hofwimmer|Brokerage|❌|✅|✅|❌|❌|✅|

|142|**sp_Kontoregister_Kontrolle**|173|Bernhard Hofwimmer|CoreBanking/BackOffice|❌|✅|❌|✅|❌|❌|

|143|**sp_Kredit_Unterschreitungen**|245|Bernhard Hofwimmer|Kreditmanagement|❌|✅|✅|✅|❌|✅|

|144|**sp_Kreditkarten_Monatlich**|520|Bernhard Hofwimmer|Kreditmanagement|❌|✅|❌|✅|❌|✅|

|145|**sp_Kunden_Cash_Only**|391|Bernhard Hofwimmer|Compliance|✅|✅|✅|✅|❌|✅|

|146|**sp_Kunden_Check_Compliance**|269|Bernhard Hofwimmer|Compliance|✅|✅|✅|❌|❌|✅|

|147|**sp_Kunden_Fluktuation**|312|Bernhard Hofwimmer|Private Banking|❌|✅|✅|✅|❌|✅|

|148|**sp_Kunden_Fluktuation_AdHoc**|188|Bernhard Hofwimmer|Private Banking|❌|✅|✅|✅|❌|✅|

|149|**sp_Kunden_Hochrisiko**|381|Bernhard Hofwimmer|Compliance|✅|✅|✅|✅|❌|✅|

|150|**sp_Kunden_Loeschung_DSGVO**|234|Bernhard Hofwimmer|IT Infrastruktur|❌|✅|✅|❌|❌|✅|

|151|**sp_Kunden_Risikoaenderung**|194|Bernhard Hofwimmer|BusinessService/Compliance|✅|✅|✅|✅|❌|✅|

|152|**sp_Kundenprofil_Depotbestand**|293|Bernhard Hofwimmer|Compliance|✅|✅|✅|❌|❌|✅|

|153|**sp_Kundensperren_Compliance**|125|Bernhard Hofwimmer|Compliance|✅|✅|❌|❌|❌|✅|

|154|**sp_Kupon_QI_Abstimmung**|485|Bernhard Hofwimmer|CoreBanking/Melderegime|✅|✅|✅|✅|❌|✅|

|155|**sp_Kurscheck_Nostro_Bestand**|269|Bernhard Hofwimmer|Treasury, Settlement|❌|✅|✅|✅|❌|✅|

|156|**sp_MIFID_Finanzinstrumente**|167|Bernhard Hofwimmer|Product Governance|✅|❌|❌|❌|❌|✅|

|157|**sp_MIFID_II_BestEx_Offenlegung**|228|Bernhard Hofwimmer|Core Banking|✅|✅|✅|❌|❌|✅|

|158|**sp_MIFIR_Transaktionen_Onbase**|156|Bernhard Hofwimmer|Settlement|✅|✅|✅|❌|❌|✅|

|159|**sp_Mailbox_vs_Spesen**|112|Bernhard Hofwimmer|Business Services|❌|✅|❌|❌|❌|✅|

|160|**sp_Mailing_Gruppen_Kunden**|337|Bernhard Hofwimmer|Marketing|❌|✅|✅|✅|❌|✅|

|161|**sp_Mailing_Kunden**|346|Bernhard Hofwimmer|Marketing|❌|✅|✅|✅|❌|✅|

|162|**sp_Manuelle_WP_Kurse**|160|Bernhard Hofwimmer|BackOffice/Settlement|❌|❌|✅|❌|❌|✅|

|163|**sp_NeuKunden_Sutor**|152|Bernhard Hofwimmer|Back Office|❌|✅|✅|❌|❌|✅|

|164|**sp_Neue_WPs_Ohne_ISIN**|153|Bernhard Hofwimmer|Settlement, Compliance|✅|❌|✅|❌|❌|✅|

|165|**sp_Neue_Wertpapiere**|184|Bernhard Hofwimmer|Private Banking|✅|✅|✅|✅|❌|✅|

|166|**sp_OENB_MELDUNG_RU_BY**|189|Bernhard Hofwimmer|Compliance|✅|✅|✅|✅|❌|✅|

|167|**sp_OTC_Dokumente**|365|Bernhard Hofwimmer|Compliance, Private Banking|✅|✅|❌|✅|❌|✅|

|168|**sp_Offene_Orders**|317|Bernhard Hofwimmer|Brokerage|❌|✅|✅|✅|❌|✅|

|169|**sp_Options**|191|Bernhard Hofwimmer|Private Banking|❌|✅|❌|✅|❌|✅|

|170|**sp_Orders_via_Navigator**|184|Bernhard Hofwimmer|Institutional PB|❌|✅|✅|❌|❌|✅|

|171|**sp_Professionelle_Kunden**|196|Bernhard Hofwimmer|Private Banking|❌|✅|❌|✅|❌|✅|

|172|**sp_Quest_Auswertung**|115|Bernhard Hofwimmer|CoreBanking (G.Tanczos)|❌|❌|✅|✅|❌|✅|

|173|**sp_Read_Impairment_Daten**|511|Bernhard Hofwimmer|Rechnungswesen / Risk Management|✅|✅|✅|❌|❌|✅|

|174|**sp_Read_Tambas_Daten_FinMgr**|621|Bernhard Hofwimmer|Core Banking|❌|✅|✅|❌|❌|✅|

|175|**sp_Realisierte_Konten**|122|Bernhard Hofwimmer|Business Services|❌|✅|❌|❌|❌|✅|

|176|**sp_Review_Nostro_Bestsand_Risk**|207|Bernhard Hofwimmer|Risk|✅|✅|✅|❌|❌|✅|

|177|**sp_Risikoklasse_Durchschnitt_VV**|328|Bernhard Hofwimmer|Matejka&Partner|✅|✅|❌|✅|❌|✅|

|178|**sp_RiskScoring_Kontrolle**|668|Bernhard Hofwimmer|Compliance|✅|✅|❌|✅|❌|✅|

|179|**sp_RiskScoring_Onbase**|362|Bernhard Hofwimmer|Private Banking|✅|✅|✅|✅|❌|✅|

|180|**sp_Risk_OENB**|403|Bernhard Hofwimmer|Kreditmanagement(Risk)|✅|✅|✅|❌|❌|✅|

|181|**sp_Risk_Review_Abgeschlossen**|211|Bernhard Hofwimmer|Compliance|✅|✅|✅|❌|❌|✅|

|182|**sp_Risk_Review_Check**|334|Bernhard Hofwimmer|Business Services|✅|✅|✅|❌|❌|✅|

|183|**sp_Risk_Review_Offen**|122|Bernhard Hofwimmer|Core Banking/Business Services|✅|❌|✅|❌|❌|✅|

|184|**sp_Risk_Review_OnBase_Details**|325|Bernhard Hofwimmer|Business Services|✅|✅|✅|✅|❌|✅|

|185|**sp_Risk_Review_OnBase_Transaktionen**|197|Bernhard Hofwimmer|Private Banking|✅|✅|✅|✅|❌|✅|

|186|**sp_Salden_KO_Sperre_CS**|204|Bernhard Hofwimmer|Back Office /Compliance|✅|✅|✅|❌|❌|✅|

|187|**sp_Send_SRD_2_CSV**|310|Bernhard Hofwimmer|BackOffice|❌|✅|✅|✅|❌|✅|

|188|**sp_SupportNet_vs_YouTrack**|145|ELSE Autor|CoreBanking|❌|❌|❌|✅|❌|❌|

|189|**sp_Ueberziehungen**|729|Bernhard Hofwimmer|Kreditmanagement|✅|✅|✅|✅|❌|✅|

|190|**sp_Ueberziehungen_Test**|694|Bernhard Hofwimmer|Kreditmanagement|✅|✅|✅|✅|❌|✅|

|191|**sp_VST_9999800011_Gegenbuchung**|147|Bernhard Hofwimmer|Buchhaltung|❌|✅|✅|❌|❌|✅|

|192|**sp_VV_Depot_Check**|166|Michaela Richtsfeld|Backoffice (R. Radeschnig)|❌|✅|✅|❌|❌|✅|

|193|**sp_VV_IP_AenderungAnlage**|282|Michaela Richtsfeld|Product Governance (A. Schwendenwein)|✅|✅|✅|❌|❌|✅|

|194|**sp_Vollmacht_Sperren**|136|Bernhard Hofwimmer|Compliance|✅|✅|❌|❌|❌|✅|

|195|**sp_Vollmachten_PEP**|197|Bernhard Hofwimmer|Compliance|✅|✅|✅|❌|❌|✅|

|196|**sp_WP_Bewegungen**|237|Bernhard Hofwimmer|Legal|❌|✅|✅|❌|❌|✅|

|197|**sp_WP_Kontrakte_Taeglich**|145|Bernhard Hofwimmer|Controlling, Brokerage|❌|✅|✅|❌|❌|✅|

|198|**sp_WP_Orders**|498|Bernhard Hofwimmer|Private Banking (Teamheads), Compliance, Business Services|✅|✅|✅|✅|❌|✅|

|199|**sp_WP_Trans_Check**|356|Bernhard Hofwimmer|Compliance|✅|✅|✅|✅|❌|✅|

|200|**sp_Write_Kunden_MonatsendDaten**|509|Bernhard Hofwimmer|Gesamtbank|❌|✅|✅|✅|❌|✅|

|201|**sp_Write_Kunden_Postfach**|276|Bernhard Hofwimmer|Business Services|❌|✅|❌|✅|❌|✅|

|202|**sp_Write_Kunden_Salden**|854|Bernhard Hofwimmer|Controlling|❌|✅|❌|✅|❌|✅|

|203|**sp_Write_Kunden_Sprache**|68|Bernhard Hofwimmer|Core Banking|❌|✅|✅|✅|❌|✅|

|204|**sp_Write_Kundenstamm_Controlling**|124|Michaela Richtsfeld|Core Banking, Controlling|❌|✅|❌|✅|❌|✅|

|205|**sp_Write_Treasury_Salden**|313|Bernhard Hofwimmer|Treasury|❌|✅|✅|❌|❌|✅|

|206|**sp_ZVK_Ausgaenge_OnBase**|187|Bernhard Hofwimmer|BO/Zahlungsverkehr|✅|✅|✅|❌|❌|✅|

|207|**sp_ZVK_Compliance**|321|Bernhard Hofwimmer|Compliance|✅|✅|✅|❌|❌|✅|

|208|**sp_ZVK_Eingang_Check24**|333|Bernhard Hofwimmer|Private Banking|❌|✅|✅|✅|❌|✅|

|209|**sp_ZVK_Eingang_Check24_Test**|332|Bernhard Hofwimmer|Private Banking (Check24)|❌|✅|✅|✅|❌|✅|

|210|**sp_ZVK_Kontrakte**|350|Bernhard Hofwimmer|Private Banking (Teamheads)|❌|✅|✅|✅|❌|✅|

|211|**sp_ZVK_Kontrakte_Ford_Verb**|235|Bernhard Hofwimmer|Rechnungswesen|❌|✅|✅|❌|❌|✅|

|212|**sp_ZVK_RU_BY_UA**|647|Bernhard Hofwimmer|Compliance|✅|✅|✅|✅|❌|✅|

|213|**sp_ZVK_Sepa_Ausgaenge_OnBase**|105|Bernhard Hofwimmer|BO/Zahlungsverkehr|❌|❌|✅|❌|❌|✅|

|214|**sp_ZVK_Taeglich**|771|Bernhard Hofwimmer|Back Office, Private Banking|✅|✅|✅|✅|❌|✅|

|215|**sp_ZVK_VS_Schwellenwert**|442|Bernhard Hofwimmer|Private Banking / Compliance|✅|✅|✅|✅|❌|✅|

|216|**sp_check_Depots_Bestand**|277|Bernhard Hofwimmer|Back Office|❌|✅|❌|✅|❌|✅|

|217|**sp_email_kundenvolumen_tipas_mailbox**|115|Hadi Aoun (Update: Florian Wugeditsch)|Core Banking|❌|✅|❌|❌|❌|❌|

|218|**sp_email_neu_angelegte_anleihen**|106|N/A|Back Office|❌|❌|✅|❌|❌|✅|

|219|**sp_email_wertpapiere_umbenennen_eng**|67|Hadi Aoun|Core Banking|❌|❌|✅|❌|❌|✅|

|220|**sp_findtext**|154|Bernhard Hofwimmer / aus AIBA bernommen|Core Banking|❌|❌|❌|✅|❌|❌|

|221|**sp_findtext_SP**|156|Bernhard Hofwimmer|Core Banking|❌|❌|❌|✅|❌|❌|

|222|**sp_kunden_ohne_volumen**|77|Hadi Aoun|Core Banking|❌|✅|❌|❌|❌|✅|

|223|**sp_mail_test**|68|Bernhard Hofwimmer|Core Banking|❌|❌|❌|❌|❌|❌|

|224|**sp_sperraenderung**|397|Bernhard Hofwimmer|Compliance|✅|✅|✅|✅|❌|✅|

|225|**sp_sperrquittierungen**|688|Bernhard Hofwimmer|Private Banking, Compliance|✅|✅|✅|✅|❌|✅|

|226|**sp_sperrquittierungen_Quartal**|224|Bernhard Hofwimmer|Private Banking (Team IPB)|✅|✅|✅|✅|❌|✅|

|227|**sp_test**|57|N/A|N/A|❌|❌|❌|❌|❌|✅|
