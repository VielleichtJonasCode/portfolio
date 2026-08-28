import numpy as np
import satelite_tools
import rockets
import constants

def start_rocket(rocket_name, payload, start_pos, target_orbit, m_prop_initial): #empty mass, fuell mass, specific impuls
    rocket = rockets.ROCKETS.get(rocket_name, None)
    transfer_results = satelite_tools.hohmann_transfer(start_pos, target_orbit) 
    needed_acceleration = transfer_results["delta_v_total"] *1000
    # Tsiolkovosky-Raketengleichung
    m_start = rocket["empty_mass_kg"] + m_prop_initial + payload
    m_end = m_start / np.exp(needed_acceleration / (rocket["isp_s"] * constants.g0))
    m_prop = m_start - m_end

    price = m_prop * rocket["fuel_price_per_kg"]

    return {
        "payload_kg": payload,
        "total_Dv": needed_acceleration,
        "propellant_used": m_prop,
        "price": price, 
        "start_mass": m_start,
        "end_mass": m_end
    }