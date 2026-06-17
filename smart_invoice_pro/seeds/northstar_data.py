"""
Static catalogue data for the NorthStar Interactive Workspace demo tenant.
"""

NORTHSTAR_ORG_NAME = "NorthStar Industrial Supplies Pvt Ltd"
NORTHSTAR_INDUSTRY = "B2B Industrial Distribution"

# (company_name, state, is_interstate)
CUSTOMERS = [
    ("Metro Engineering Pvt Ltd", "Maharashtra", False),
    ("Prime Industrial Works", "Gujarat", True),
    ("Eastern Infrastructure Solutions", "West Bengal", True),
    ("Vertex Manufacturing Co", "Karnataka", True),
    ("Bharat Automation Systems", "Tamil Nadu", True),
    ("Horizon Manufacturing Ltd", "Maharashtra", False),
    ("Apex Engineering Solutions", "Karnataka", True),
    ("Sterling Packaging Industries", "Gujarat", True),
    ("Metro Retail Distribution", "Maharashtra", False),
    ("Delta Infrastructure Services", "Tamil Nadu", True),
    ("Precision Tools India", "Maharashtra", False),
    ("National Fabricators LLP", "Rajasthan", True),
    ("Coastal Marine Supplies", "Kerala", True),
    ("Punjab Agro Equipment Co", "Punjab", True),
    ("Western Power Components", "Maharashtra", False),
    ("Global Steel Traders", "Gujarat", True),
    ("Sunrise Electricals", "Telangana", True),
    ("BlueLine Logistics Partners", "Maharashtra", False),
    ("Omni Build Projects", "Delhi", True),
    ("Rajasthan Mining Tools", "Rajasthan", True),
    ("Chennai Process Industries", "Tamil Nadu", True),
    ("Hyderabad Tech Fabricators", "Telangana", True),
    ("Lucknow Industrial Mart", "Uttar Pradesh", True),
    ("Indore Machine Works", "Madhya Pradesh", True),
    ("Kolkata Port Services", "West Bengal", True),
    ("Ahmedabad Chemical Supply", "Gujarat", True),
    ("Nashik Auto Components", "Maharashtra", False),
    ("Bhubaneswar Construction Co", "Odisha", True),
    ("Jaipur Safety Solutions", "Rajasthan", True),
    ("Surat Textile Machinery", "Gujarat", True),
    ("Vadodara Pump Systems", "Gujarat", True),
    ("Nagpur Warehouse Services", "Maharashtra", False),
    ("Patna Engineering House", "Bihar", True),
    ("Kochi Offshore Supplies", "Kerala", True),
]

# (vendor_name, state)
VENDORS = [
    ("Prime Industrial Components", "Maharashtra"),
    ("Zenith Packaging Materials", "Gujarat"),
    ("Bharat Logistics Services", "Maharashtra"),
    ("Allied Electrical Traders", "Karnataka"),
    ("National Fasteners Corp", "Maharashtra"),
    ("Southern Steel Suppliers", "Tamil Nadu"),
    ("Western Hydraulics Pvt Ltd", "Gujarat"),
    ("Eastern Safety Gear Co", "West Bengal"),
    ("Pune Precision Castings", "Maharashtra"),
    ("Delhi Power Cables Ltd", "Delhi"),
    ("Chennai Bearing House", "Tamil Nadu"),
    ("Hyderabad Lab Chemicals", "Telangana"),
    ("Rajasthan Minerals Trading", "Rajasthan"),
    ("Kolkata Import House", "West Bengal"),
    ("Mumbai Industrial Gases", "Maharashtra"),
    ("Surat Polymer Solutions", "Gujarat"),
    ("Coimbatore Motors India", "Tamil Nadu"),
    ("Indore Tooling Works", "Madhya Pradesh"),
    ("Lucknow Packaging Hub", "Uttar Pradesh"),
    ("Kerala Rubber Products", "Kerala"),
]

# (name, category, unit, selling_price, reorder_level, tax_rate, hsn_sac)
PRODUCTS = [
    ("Industrial Safety Gloves", "Safety Equipment", "Nos", 450, 120, 18, "6116"),
    ("Stainless Fasteners M8", "Hardware", "Nos", 85, 500, 18, "7318"),
    ("Packaging Cartons 18x12", "Packaging", "Nos", 120, 800, 12, "4819"),
    ("Electrical Control Panels", "Electrical", "Nos", 18500, 15, 18, "8537"),
    ("PVC Insulation Tape", "Electrical", "Nos", 35, 200, 18, "3919"),
    ("Hydraulic Hose Assembly", "Industrial", "Nos", 2200, 40, 18, "4009"),
    ("Warehouse Labels Roll", "Packaging", "Roll", 280, 150, 12, "4821"),
    ("LED Flood Light 50W", "Electrical", "Nos", 1650, 60, 18, "9405"),
    ("Angle Grinder 4 inch", "Power Tools", "Nos", 3200, 25, 18, "8467"),
    ("Welding Electrode 3.2mm", "Welding", "Kg", 180, 300, 18, "8311"),
    ("Industrial Lubricant 20L", "Chemicals", "Can", 4200, 30, 18, "2710"),
    ("Conveyor Belt Section", "Industrial", "Mtr", 850, 50, 18, "4010"),
    ("Safety Helmet ISI", "Safety Equipment", "Nos", 320, 200, 18, "6506"),
    ("M8 Hex Bolt Set", "Hardware", "Set", 95, 400, 18, "7318"),
    ("Cable Tray 300mm", "Electrical", "Mtr", 680, 80, 18, "7326"),
    ("Pallet Wrap Film", "Packaging", "Roll", 420, 120, 12, "3920"),
    ("Fire Extinguisher 6kg", "Safety Equipment", "Nos", 2800, 20, 18, "8424"),
    ("Air Compressor Filter", "Industrial", "Nos", 1450, 35, 18, "8421"),
    ("Digital Multimeter", "Electrical", "Nos", 980, 45, 18, "9030"),
    ("Chain Block 1 Ton", "Material Handling", "Nos", 12500, 8, 18, "8425"),
    ("Rubber Gasket Sheet", "Industrial", "Sheet", 650, 60, 18, "4016"),
    ("Paint Spray Gun", "Tools", "Nos", 2100, 18, 18, "8424"),
    ("Steel Pipes 2 inch", "Hardware", "Mtr", 420, 150, 18, "7306"),
    ("Industrial Fan 24 inch", "Electrical", "Nos", 5400, 12, 18, "8414"),
    ("Forklift Battery Charger", "Electrical", "Nos", 28000, 5, 18, "8504"),
    ("Workshop Bench Vice", "Tools", "Nos", 3800, 15, 18, "8205"),
    ("Dust Mask N95 Pack", "Safety Equipment", "Pack", 240, 250, 12, "6307"),
    ("Hydraulic Oil 5L", "Chemicals", "Can", 890, 100, 18, "2710"),
]

PAYMENT_TERMS = ["Net 15", "Net 30", "Net 45", "Due on Receipt"]
CREDIT_LIMITS = [250000, 500000, 750000, 1000000, 1500000, 2000000]
