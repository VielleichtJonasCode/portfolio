import physics.constants as constants
import numpy as np

def hohmann_transfer(r1, r2):
    v1 = np.sqrt(constants.my_earth/r1) # Geschwindigkeiten auf Kreisbahn
    v2 = np.sqrt(constants.my_earth/r2)
    at = (r1 + r2) / 2

    #Vis-Viva-Gleichung
    vt1 = np.sqrt(constants.my_earth * (2/r1 - 1/at))
    vt2 = np.sqrt(constants.my_earth * (2/r2 - 1/at))

    #Berechne den Treibstoffaufwand:
    Dv1 = abs(vt1 - v1)
    Dv2 = abs(v2 - vt2)
    Dv = Dv1 + Dv2

    #Berechen die Transferzeit
    Ttof = np.pi * np.sqrt((at**3) / constants.my_earth)

    return {
        "delta_v1": float(Dv1),
        "delta_v2": float(Dv2),
        "delta_v_total": float(Dv),
        "transfer_time_seconds": float(Ttof),
        "transfer_time_hours": float(Ttof / 3600)
    }

def velocity_change(r, current_v, delta_v):
    v_new = current_v + delta_v
    #spezifische Orbitalenergie
    specific_energy = (v_new ** 2) / 2 - (constants.GM / r)
    if specific_energy >= 0:   
        return {"status": "Escape Orbit"}
    a = -constants.GM / (2 * specific_energy)

    r_other = 2 * a -r 
    perigee = min(r, r_other)
    apogee = max(r, r_other)

    return {
        "new_velocity": v_new,
        "semi_major_axis_km": a,
        "perigee_km": perigee,
        "apogee_km": apogee
    }
