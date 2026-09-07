import sqlite3

# Make sure this points to your actual database file
db_path = "compliance.db" 

def wipe_history():
    conn = sqlite3.connect(db_path)
    cursor = conn.cursor()
    
    print("Wiping inspection history...")
    
    try:
        # 1. Delete all violations (child table)
        cursor.execute("DELETE FROM violations;")
        
        # 2. Delete all font calibration records
        cursor.execute("DELETE FROM font_check_records;")
        
        # 3. Delete all main scan results (parent table)
        cursor.execute("DELETE FROM scan_results;")
        
        conn.commit()
        print("Success! History completely wiped. Your officer logins are safe.")
    except Exception as e:
        print(f"Error: {e}")
    finally:
        conn.close()

if __name__ == "__main__":
    wipe_history()