# launch.py - Sprunki Ultimate Master Launcher
# Convenient launcher with pre-flight checks and error handling

import os
import sys
import json
import time
import subprocess
from pathlib import Path

def print_banner():
    """Print application banner"""
    print("🎮" + "="*70 + "🎮")
    print("🚀            SPRUNKI ULTIMATE MASTER LAUNCHER            🚀")
    print("⚡" + "="*70 + "⚡")
    print("   Unified Pipeline: URL List → JSON → Professional Videos")
    print("="*74)

def check_python_version():
    """Check Python version compatibility"""
    version = sys.version_info
    if version.major < 3 or (version.major == 3 and version.minor < 8):
        print("❌ Python 3.8+ required")
        print(f"   Current: Python {version.major}.{version.minor}.{version.micro}")
        print("   Please upgrade Python and try again")
        return False
    
    print(f"✅ Python {version.major}.{version.minor}.{version.micro}")
    return True

def check_main_file():
    """Check if main application file exists"""
    main_file = "sprunki_ultimate_master.py"
    if not os.path.exists(main_file):
        print(f"❌ Main file missing: {main_file}")
        print("   Please ensure all project files are in the current directory")
        return False
    
    print(f"✅ Main application file found: {main_file}")
    return True

def check_required_modules():
    """Quick check for essential modules"""
    required_modules = [
        'tkinter', 'pyautogui', 'cv2', 'requests', 'websocket'
    ]
    
    missing_modules = []
    for module in required_modules:
        try:
            __import__(module)
        except ImportError:
            missing_modules.append(module)
    
    if missing_modules:
        print(f"❌ Missing modules: {', '.join(missing_modules)}")
        print("   Run: pip install -r requirements.txt")
        return False
    
    print("✅ Essential modules available")
    return True

def check_config_file():
    """Check configuration file"""
    config_file = "config.json"
    if not os.path.exists(config_file):
        print(f"⚠️  Config file missing: {config_file}")
        print("   Creating default configuration...")
        create_default_config()
        return True
    
    try:
        with open(config_file, 'r', encoding='utf-8') as f:
            config = json.load(f)
        print(f"✅ Configuration loaded: {config_file}")
        return True
    except json.JSONDecodeError:
        print(f"❌ Invalid JSON in: {config_file}")
        return False

def create_default_config():
    """Create a minimal default configuration"""
    default_config = {
        "obs": {
            "host": "localhost",
            "port": 4455,
            "password": "change_this_password"
        },
        "output": {
            "base_directory": "C:\\Users\\Videos",
            "create_project_folders": True
        },
        "game": {
            "default_timeout": 30
        }
    }
    
    try:
        with open("config.json", 'w', encoding='utf-8') as f:
            json.dump(default_config, f, indent=2)
        print("✅ Default config created")
    except Exception as e:
        print(f"❌ Could not create config: {e}")

def check_directories():
    """Ensure necessary directories exist"""
    directories = [
        "image_assets/cocrea.world",
        "logs", 
        "temp",
        "output"
    ]
    
    for directory in directories:
        Path(directory).mkdir(parents=True, exist_ok=True)
    
    print("✅ Directory structure ready")
    return True

def check_image_assets():
    """Check for required image assets"""
    assets_dir = Path("image_assets/cocrea.world")
    required_assets = ["run_button.png", "fullscreen_button.png"]
    
    missing_assets = []
    for asset in required_assets:
        if not (assets_dir / asset).exists():
            missing_assets.append(asset)
    
    if missing_assets:
        print(f"⚠️  Missing image assets: {', '.join(missing_assets)}")
        print(f"   Add them to: {assets_dir}/")
        print("   ⚠️  JSON generation may fail without these assets")
        return False
    
    print("✅ Image assets ready")
    return True

def show_startup_menu():
    """Show startup options menu"""
    print("\n📋 STARTUP OPTIONS:")
    print("   1. 🚀 Launch with full GUI")
    print("   2. 🧪 Run setup validation tests")
    print("   3. 🔧 Open configuration file")
    print("   4. 📖 Show quick help")
    print("   5. ❌ Exit")
    
    while True:
        try:
            choice = input("\nSelect option (1-5): ").strip()
            
            if choice == "1":
                return "launch"
            elif choice == "2":
                return "test"
            elif choice == "3":
                return "config"
            elif choice == "4":
                return "help"
            elif choice == "5":
                return "exit"
            else:
                print("❌ Invalid choice. Please enter 1-5.")
                
        except (KeyboardInterrupt, EOFError):
            print("\n👋 Goodbye!")
            return "exit"

def run_tests():
    """Run setup validation tests"""
    if os.path.exists("test_setup.py"):
        print("\n🧪 Running setup validation tests...")
        try:
            result = subprocess.run([sys.executable, "test_setup.py"], 
                                  capture_output=False, text=True)
            return result.returncode == 0
        except Exception as e:
            print(f"❌ Could not run tests: {e}")
            return False
    else:
        print("❌ Test file not found: test_setup.py")
        return False

def open_config():
    """Open configuration file in default editor"""
    config_file = "config.json"
    try:
        if sys.platform.startswith('win'):
            os.startfile(config_file)
        elif sys.platform.startswith('darwin'):
            subprocess.run(['open', config_file])
        else:
            subprocess.run(['xdg-open', config_file])
        
        print(f"✅ Opened config file: {config_file}")
        print("   Update OBS settings and save the file")
        
    except Exception as e:
        print(f"❌ Could not open config file: {e}")
        print(f"   Please edit manually: {config_file}")

def show_help():
    """Show quick help information"""
    help_text = """
📖 SPRUNKI ULTIMATE MASTER - QUICK HELP

🎯 WHAT IT DOES:
   Automates the complete workflow from Sprunki game URLs to professional videos
   
🚀 MAIN FEATURES:
   • URL List → Auto JSON Generation → Video Processing
   • Chrome automation with DevTools Protocol
   • OBS recording control via WebSocket
   • Organized output with Master + Individual modes
   
📋 BASIC USAGE:
   1. Launch application
   2. Click "Add URL List" 
   3. Paste Sprunki game URLs (one per line)
   4. Configure processing modes (Both/Master/Individual)
   5. Click "START UNIFIED PIPELINE"
   6. Monitor progress and wait for completion
   
🔧 REQUIREMENTS:
   • Python 3.8+
   • Google Chrome browser
   • OBS Studio with WebSocket enabled
   • Image assets for UI detection
   
⚙️ CONFIGURATION:
   • Edit config.json for OBS settings
   • Update output directory paths
   • Adjust timeouts and retry settings
   
🆘 TROUBLESHOOTING:
   • Run setup validation: python test_setup.py
   • Check logs/ directory for error details
   • Ensure OBS WebSocket is enabled (port 4455)
   • Verify Chrome installation and assets
   
📚 DOCUMENTATION:
   • README.md - Complete usage guide
   • MIGRATION_GUIDE.md - Upgrade instructions
   • config.json - All settings explained
   
🎮 READY TO START? Choose option 1 from the menu!
"""
    print(help_text)

def launch_application():
    """Launch the main application"""
    print("\n🚀 Launching Sprunki Ultimate Master...")
    print("="*50)
    
    try:
        # Import and run the main application
        from sprunki_ultimate_master import SprunkiUltimateMaster
        
        print("✅ Main application loaded")
        print("🎮 Starting GUI interface...")
        
        app = SprunkiUltimateMaster()
        app.run()
        
    except ImportError as e:
        print(f"❌ Could not import main application: {e}")
        print("   Please ensure sprunki_ultimate_master.py is in the current directory")
        return False
    except Exception as e:
        print(f"❌ Application error: {e}")
        import traceback
        traceback.print_exc()
        return False
    
    return True

def main():
    """Main launcher function with config initialization"""
    print_banner()
    
    # Initialize configuration first
    try:
        from config_manager import initialize_config
        config = initialize_config("config.json")
        print("✅ Centralized configuration initialized")
    except Exception as e:
        print(f"⚠️ Warning: Could not load config.json: {e}")
        print("   Using default settings...")    
    
    # Pre-flight checks
    print("\n🔍 PRE-FLIGHT CHECKS:")
    print("-" * 30)
    
    checks = [
        ("Python Version", check_python_version),
        ("Main Application File", check_main_file),
        ("Essential Modules", check_required_modules),
        ("Configuration File", check_config_file),
        ("Directory Structure", check_directories)
    ]
    
    all_checks_passed = True
    for check_name, check_func in checks:
        print(f"\n🔍 {check_name}:")
        if not check_func():
            all_checks_passed = False
    
    # Optional checks (warnings only)
    print(f"\n⚠️  Optional Checks:")
    check_image_assets()
    
    if not all_checks_passed:
        print("\n❌ SOME CHECKS FAILED")
        print("   Please resolve the issues above before launching")
        print("   Run: python setup.py for automated setup")
        input("\n   Press Enter to exit...")
        return 1
    
    print("\n✅ PRE-FLIGHT CHECKS COMPLETED")
    
    # Show menu and handle user choice
    while True:
        choice = show_startup_menu()
        
        if choice == "launch":
            success = launch_application()
            if success:
                print("\n👋 Application closed successfully")
            return 0 if success else 1
            
        elif choice == "test":
            test_success = run_tests()
            if test_success:
                print("\n✅ All tests passed!")
            else:
                print("\n❌ Some tests failed. Please check the output above.")
            input("\nPress Enter to continue...")
            
        elif choice == "config":
            open_config()
            input("\nPress Enter to continue...")
            
        elif choice == "help":
            show_help()
            input("\nPress Enter to continue...")
            
        elif choice == "exit":
            print("\n👋 Goodbye!")
            return 0

if __name__ == "__main__":
    try:
        exit_code = main()
        sys.exit(exit_code)
    except KeyboardInterrupt:
        print("\n\n⏹️  Interrupted by user")
        sys.exit(2)
    except Exception as e:
        print(f"\n💥 Unexpected error: {e}")
        import traceback
        traceback.print_exc()
        input("\nPress Enter to exit...")
        sys.exit(3)