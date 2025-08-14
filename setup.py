# setup.py - Sprunki Ultimate Master Installation Script
# Usage: python setup.py install

import os
import sys
import subprocess
import urllib.request
import zipfile
from pathlib import Path

def print_step(step_num, description):
    """Print formatted setup step"""
    print(f"\n🔧 STEP {step_num}: {description}")
    print("=" * 50)

def check_python_version():
    """Verify Python version compatibility"""
    print_step(1, "Checking Python Version")
    
    version = sys.version_info
    if version.major < 3 or (version.major == 3 and version.minor < 8):
        print("❌ Python 3.8+ required")
        print(f"   Current version: {version.major}.{version.minor}.{version.micro}")
        print("   Please upgrade Python and try again")
        return False
    
    print(f"✅ Python {version.major}.{version.minor}.{version.micro} detected")
    return True

def install_requirements():
    """Install Python dependencies"""
    print_step(2, "Installing Python Dependencies")
    
    try:
        # Install main requirements
        subprocess.check_call([
            sys.executable, "-m", "pip", "install", "-r", "requirements.txt"
        ])
        print("✅ Python packages installed successfully")
        return True
    except subprocess.CalledProcessError as e:
        print(f"❌ Failed to install requirements: {e}")
        return False

def setup_directories():
    """Create necessary directories"""
    print_step(3, "Setting up Directories")
    
    directories = [
        "image_assets",
        "image_assets/cocrea.world",
        "output",
        "logs",
        "temp",
        "backup"
    ]
    
    for directory in directories:
        Path(directory).mkdir(parents=True, exist_ok=True)
        print(f"📁 Created: {directory}/")
    
    print("✅ Directory structure created")
    return True

def download_image_assets():
    """Download required image assets"""
    print_step(4, "Downloading Image Assets")
    
    assets_dir = Path("image_assets/cocrea.world")
    
    # Image assets needed for detection
    required_assets = [
        "run_button.png",
        "fullscreen_button.png"
    ]
    
    print("📋 Required image assets:")
    for asset in required_assets:
        asset_path = assets_dir / asset
        if not asset_path.exists():
            print(f"   ⚠️  Missing: {asset}")
            print(f"      Please add manually to: {asset_path}")
        else:
            print(f"   ✅ Found: {asset}")
    
    print("\n💡 MANUAL SETUP REQUIRED:")
    print("   1. Open cocrea.world in browser")
    print("   2. Take screenshots of 'Run' and 'Fullscreen' buttons")
    print("   3. Save as run_button.png and fullscreen_button.png")
    print(f"   4. Place in: {assets_dir}/")
    
    return True

def check_chrome_installation():
    """Verify Chrome installation"""
    print_step(5, "Checking Chrome Installation")
    
    chrome_paths = [
        r"C:\Program Files\Google\Chrome\Application\chrome.exe",
        r"C:\Program Files (x86)\Google\Chrome\Application\chrome.exe"
    ]
    
    chrome_found = False
    for path in chrome_paths:
        if os.path.exists(path):
            print(f"✅ Chrome found: {path}")
            chrome_found = True
            break
    
    if not chrome_found:
        print("❌ Google Chrome not found")
        print("   Please install Chrome from: https://chrome.google.com")
        print("   Required for JSON generation functionality")
        return False
    
    return True

def check_obs_installation():
    """Check OBS Studio installation"""
    print_step(6, "Checking OBS Studio")
    
    obs_paths = [
        r"C:\Program Files\obs-studio\bin\64bit\obs64.exe",
        r"C:\Program Files (x86)\obs-studio\bin\32bit\obs32.exe"
    ]
    
    obs_found = False
    for path in obs_paths:
        if os.path.exists(path):
            print(f"✅ OBS Studio found: {path}")
            obs_found = True
            break
    
    if not obs_found:
        print("⚠️  OBS Studio not found")
        print("   Download from: https://obsproject.com")
        print("   Required for video recording functionality")
    
    print("\n🔧 OBS SETUP CHECKLIST:")
    print("   □ Install OBS Studio")
    print("   □ Enable WebSocket Server (Tools → WebSocket Server Settings)")
    print("   □ Set Port: 4455")
    print("   □ Set Password (update in sprunki_obs_controller.py)")
    print("   □ Create 'Sprunki Capture' Display Capture source")
    
    return True

def create_desktop_shortcut():
    """Create desktop shortcut (Windows)"""
    print_step(7, "Creating Desktop Shortcut")
    
    if sys.platform != "win32":
        print("⚠️  Shortcut creation only supported on Windows")
        return True
    
    try:
        import win32com.client
        
        desktop = os.path.join(os.path.expanduser("~"), "Desktop")
        shortcut_path = os.path.join(desktop, "Sprunki Ultimate Master.lnk")
        
        shell = win32com.client.Dispatch("WScript.Shell")
        shortcut = shell.CreateShortCut(shortcut_path)
        shortcut.Targetpath = sys.executable
        shortcut.Arguments = os.path.join(os.getcwd(), "sprunki_ultimate_master.py")
        shortcut.WorkingDirectory = os.getcwd()
        shortcut.IconLocation = sys.executable
        shortcut.save()
        
        print(f"✅ Desktop shortcut created: {shortcut_path}")
        return True
        
    except ImportError:
        print("⚠️  pywin32 not installed, skipping shortcut creation")
        print("   Install with: pip install pywin32")
        return True
    except Exception as e:
        print(f"⚠️  Could not create desktop shortcut: {e}")
        return True

def create_config_file():
    """Create default configuration file"""
    print_step(8, "Creating Configuration File")
    
    config = {
        "obs": {
            "host": "localhost",
            "port": 4455,
            "password": "change_this_password"
        },
        "output": {
            "base_directory": "C:\\Users\\[username]\\Videos",
            "create_project_folders": True,
            "video_quality": "high"
        },
        "game": {
            "default_domain": "cocrea.world",
            "timeout_seconds": 30,
            "retry_attempts": 3
        },
        "ui": {
            "theme": "default",
            "show_advanced_options": False,
            "auto_save_logs": True
        }
    }
    
    config_path = "config.json"
    if not os.path.exists(config_path):
        with open(config_path, 'w', encoding='utf-8') as f:
            import json
            json.dump(config, f, indent=2)
        print(f"✅ Configuration file created: {config_path}")
    else:
        print(f"✅ Configuration file exists: {config_path}")
    
    print("\n🔧 CONFIGURATION CHECKLIST:")
    print(f"   □ Edit {config_path}")
    print("   □ Update OBS password")
    print("   □ Set correct output directory")
    print("   □ Adjust timeout settings if needed")
    
    return True

def run_test():
    """Run basic functionality test"""
    print_step(9, "Running Basic Tests")
    
    try:
        # Test imports
        print("🧪 Testing module imports...")
        
        import tkinter
        print("   ✅ tkinter")
        
        import pyautogui
        print("   ✅ pyautogui")
        
        import cv2
        print("   ✅ opencv-python")
        
        import requests
        print("   ✅ requests")
        
        import websocket
        print("   ✅ websocket-client")
        
        print("\n✅ All core modules imported successfully")
        
        # Test basic functionality
        print("\n🧪 Testing basic functionality...")
        
        # Test screen size detection
        screen_width, screen_height = pyautogui.size()
        print(f"   ✅ Screen resolution: {screen_width}x{screen_height}")
        
        # Test file operations
        test_file = "test_setup.tmp"
        with open(test_file, 'w') as f:
            f.write("setup test")
        os.remove(test_file)
        print("   ✅ File operations")
        
        print("\n✅ Basic functionality test passed")
        return True
        
    except ImportError as e:
        print(f"❌ Import test failed: {e}")
        return False
    except Exception as e:
        print(f"❌ Functionality test failed: {e}")
        return False

def print_final_instructions():
    """Print final setup instructions"""
    print("\n" + "=" * 60)
    print("🎉 SETUP COMPLETED!")
    print("=" * 60)
    
    print("\n📋 NEXT STEPS:")
    print("   1. 📸 Add image assets to image_assets/cocrea.world/")
    print("      - run_button.png")
    print("      - fullscreen_button.png")
    print("   ")
    print("   2. 🔧 Configure OBS Studio:")
    print("      - Enable WebSocket Server")
    print("      - Create 'Sprunki Capture' source")
    print("      - Update password in config.json")
    print("   ")
    print("   3. 🚀 Launch the application:")
    print("      python sprunki_ultimate_master.py")
    print("   ")
    print("   4. 🧪 Test with a single URL first")
    
    print("\n📚 DOCUMENTATION:")
    print("   - README.md - Complete usage guide")
    print("   - MIGRATION_GUIDE.md - Upgrade instructions")
    print("   - config.json - Configuration settings")
    
    print("\n🆘 SUPPORT:")
    print("   - Check console logs for errors")
    print("   - Verify all requirements are met")
    print("   - Test components individually")
    
    print("\n🎮 READY TO AUTOMATE YOUR SPRUNKI WORKFLOW!")

def main():
    """Main setup function"""
    print("🎮 SPRUNKI ULTIMATE MASTER - SETUP WIZARD")
    print("=" * 60)
    
    # Run setup steps
    steps = [
        check_python_version,
        install_requirements,
        setup_directories,
        download_image_assets,
        check_chrome_installation,
        check_obs_installation,
        create_desktop_shortcut,
        create_config_file,
        run_test
    ]
    
    for step in steps:
        if not step():
            print(f"\n❌ Setup failed at: {step.__name__}")
            print("   Please resolve the issue and run setup again")
            sys.exit(1)
        
        input("\n   Press Enter to continue...")
    
    print_final_instructions()
    print("\n✅ Setup completed successfully!")

if __name__ == "__main__":
    main()