import sqlite3

# Update this path if your compliance.db is located somewhere else
# Based on standard setups, it's usually in backend/compliance.db or backend/app/compliance.db
db_path = "compliance.db"

def upgrade_database():
    conn = sqlite3.connect(db_path)
    cursor = conn.cursor()
    
    print("Checking database schema...")
    
    try:
        # Safely attempt to add the new columns
        cursor.execute("ALTER TABLE scan_results ADD COLUMN calibrator_used BOOLEAN DEFAULT 0;")
        print("Added column: calibrator_used")
    except sqlite3.OperationalError as e:
        if "duplicate column name" in str(e):
            print("Column 'calibrator_used' already exists. Skipping.")
        else:
            print(f"Error: {e}")

    try:
        cursor.execute("ALTER TABLE scan_results ADD COLUMN calibrator_result VARCHAR(50);")
        print("Added column: calibrator_result")
    except sqlite3.OperationalError as e:
        if "duplicate column name" in str(e):
            print("Column 'calibrator_result' already exists. Skipping.")
        else:
            print(f"Error: {e}")

    # Also add the new Font Calibration table if it's missing
    cursor.execute("""
    CREATE TABLE IF NOT EXISTS font_check_records (
        id CHAR(36) PRIMARY KEY,
        officer_id CHAR(36),
        image_path VARCHAR(500) NOT NULL,
        coin_key VARCHAR(50) NOT NULL,
        tap_x INTEGER NOT NULL,
        tap_y INTEGER NOT NULL,
        net_quantity_g_or_ml FLOAT,
        measured_mm FLOAT,
        required_mm FLOAT,
        is_compliant BOOLEAN,
        raw_result JSON,
        created_at DATETIME NOT NULL,
        FOREIGN KEY(officer_id) REFERENCES users(id) ON DELETE SET NULL
    );
    """)
    print("Checked/Created table: font_check_records")

    conn.commit()
    conn.close()
    print("Database upgrade complete! You can now start your FastAPI server.")

if __name__ == "__main__":
    upgrade_database()