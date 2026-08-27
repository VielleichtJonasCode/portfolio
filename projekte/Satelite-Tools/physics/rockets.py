# physics/rockets.py

ROCKETS = {
    "falcon_9": {
        "name": "SpaceX Falcon 9 (Oberstufe)",
        "empty_mass_kg": 4000,
        "propellant_mass_kg": 107500,
        "isp_s": 348,
        "fuel_price_per_kg": 3.50  # Günstiger (RP-1 / LOX)
    },
    "ariane_5": {
        "name": "Ariane 5 (Oberstufe ESC-A)",
        "empty_mass_kg": 4540,
        "propellant_mass_kg": 14000,
        "isp_s": 446,
        "fuel_price_per_kg": 12.00 # Teurer (Flüssigwasserstoff / LH2 / LOX)
    },
    "ariane_6": {
        "name": "Ariane 6 (Oberstufe / Upper Stage)",
        "empty_mass_kg": 2300,
        "propellant_mass_kg": 5000,
        "isp_s": 465,
        "fuel_price_per_kg": 12.00 # Auch flüssiger Wasserstoff
    },
    "saturn_v": {
        "name": "Saturn V (3. Stufe - S-IVB)",
        "empty_mass_kg": 13500,
        "propellant_mass_kg": 109600,
        "isp_s": 421,
        "fuel_price_per_kg": 10.00
    },
    "atlas_v": {
        "name": "Atlas V (Centaur Oberstufe)",
        "empty_mass_kg": 2300,
        "propellant_mass_kg": 20830,
        "isp_s": 451,
        "fuel_price_per_kg": 11.50 # Flüssigwasserstoff
    },
    "soyuz": {
        "name": "Sojus-2 (Fregat Oberstufe)",
        "empty_mass_kg": 1050,
        "propellant_mass_kg": 5250,
        "isp_s": 335,
        "fuel_price_per_kg": 4.00  # Kerosin / UDMH
    },
    "electron": {
        "name": "Rocket Lab Electron (Kick Stage / Curie)",
        "empty_mass_kg": 50,
        "propellant_mass_kg": 150,
        "isp_s": 320,
        "fuel_price_per_kg": 15.00 # Spezialtreibstoff für kleine Kick-Stufen
    }
}
