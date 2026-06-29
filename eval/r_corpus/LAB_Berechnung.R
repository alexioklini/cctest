# Lade ben?tigte Funktionen
source("Ablaufmodellierung.R")

# Import Durchschnittssalden
LAB5 = read.csv("R:/Risikomanagement/Liquidität/Bodensatz/Aktualisierung Bodensätze für 2026/Datenbasis/Input_Durchschnittssalden_2025.csv", header = TRUE, sep = ";", dec = ".", fill = TRUE, stringsAsFactors = FALSE)

# Ausf?hrung Berechnung
StochastikTool(LAB5)

