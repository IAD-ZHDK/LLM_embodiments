import os
import sys
import requests
import zipfile
import io
import shutil

def download_and_extract_model(model_name, output_dir, base_url=None):
    """
    Download and extract a model, handling nested directories.
    
    Args:
        model_name (str): Name of the model to download
        output_dir (str): Directory where model should be extracted
        base_url (str, optional): Base URL for downloading models.
                                 If None, tries to determine appropriate URL.
    
    Returns:
        bool: True if download and extraction successful, False otherwise
    """
    # Determine model type and base URL if not provided
    if base_url is None:
        if model_name.endswith(".onnx"):
            # TTS model - Piper voices
            base_url = "https://huggingface.co/rhasspy/piper-voices/resolve/v1.0.0/"
        elif "vosk" in model_name.lower():
            # STT model - Vosk
            base_url = "https://alphacephei.com/vosk/models/"
            print(f"name of models: {model_name}", file=sys.stderr)
            if not model_name.endswith(".zip"):
                print(f"adding .zip to {model_name}", file=sys.stderr)
                model_name = f"{model_name}.zip"
        else:
            
            # Try to guess based on file extension
            if model_name.endswith(".zip"):
                base_url = "https://alphacephei.com/vosk/models/"
            else:
                print(f"Could not determine base URL for model: {model_name}", file=sys.stderr)
                print(f"Please provide a base_url parameter.", file=sys.stderr)
                return False
    
    # Construct download URL
    download_url = f"{base_url}{model_name}"
    print(f"Downloading model from {download_url}", file=sys.stderr)
    
    # Create output directory if it doesn't exist
    os.makedirs(output_dir, exist_ok=True)
    
    try:
        # Download the model
        print(f"Starting download of {model_name}...", file=sys.stderr)
        response = requests.get(download_url, stream=True)
        response.raise_for_status()
        
        # For direct file downloads (like .onnx files)
        if not model_name.endswith(".zip"):
            output_path = os.path.join(output_dir, os.path.basename(model_name))
            with open(output_path, 'wb') as f:
                shutil.copyfileobj(response.raw, f)
            print(f"Model downloaded to {output_path}", file=sys.stderr)
            return True
        
        # For zipped models
        print("Extracting ZIP archive...", file=sys.stderr)
        with zipfile.ZipFile(io.BytesIO(response.content)) as z:
            # Get top-level directories in the zip
            top_dirs = {item.split('/')[0] for item in z.namelist() if '/' in item}
            
            # Handle different zip structures
            model_base_name = os.path.splitext(model_name)[0]  # Remove .zip extension
            model_output_dir = os.path.join(output_dir, model_base_name)
            
            # Check if the zip has a single top directory with the model name
            if len(top_dirs) == 1 and list(top_dirs)[0] == model_base_name:
                # The zip already has a directory with the model name
                z.extractall(output_dir)
                print(f"Model extracted to {model_output_dir}", file=sys.stderr)
            else:
                # Create model directory and extract there
                os.makedirs(model_output_dir, exist_ok=True)
                z.extractall(model_output_dir)
                print(f"Model extracted to {model_output_dir}", file=sys.stderr)
                
        print(f"Model '{model_name}' downloaded and extracted successfully.", file=sys.stderr)
        return True
    except requests.RequestException as e:
        print(f"Error downloading model: {e}", file=sys.stderr)
        return False
    except zipfile.BadZipFile as e:
        print(f"Downloaded file is not a valid zip file: {e}", file=sys.stderr)
        return False
    except Exception as e:
        print(f"Error processing model: {e}", file=sys.stderr)
        return False

def download_piper_voice(model_name, output_dir, model_url=None, config_url=None):
    """
    Download Piper voice model and config files from specified URLs.
    
    Args:
        model_name (str): Base name for the model (without extension)
        output_dir (str): Directory where files should be downloaded
        model_url (str, optional): URL for the ONNX model file
        config_url (str, optional): URL for the JSON config file
        
    Returns:
        bool: True if both files downloaded successfully, False otherwise
    """
    # Create output directory if it doesn't exist
    os.makedirs(output_dir, exist_ok=True)
    
    # If URLs not provided, use defaults from Hugging Face
    if model_url is None:
        model_url = f"https://huggingface.co/rhasspy/piper-voices/resolve/v1.0.0/{model_name}.onnx"
    
    if config_url is None:
        config_url = f"https://huggingface.co/rhasspy/piper-voices/resolve/v1.0.0/{model_name}.onnx.json"
    
    # Output file paths
    model_path = os.path.join(output_dir, f"{model_name}.onnx")
    config_path = os.path.join(output_dir, f"{model_name}.onnx.json")
    
    try:
        # Download the ONNX model
        print(f"Downloading model from {model_url}", file=sys.stderr)
        model_response = requests.get(model_url, stream=True)
        model_response.raise_for_status()
        
        with open(model_path, 'wb') as f:
            shutil.copyfileobj(model_response.raw, f)
        
        print(f"Model downloaded to {model_path}", file=sys.stderr)
        
        # Download the config file
        print(f"Downloading config from {config_url}", file=sys.stderr)
        config_response = requests.get(config_url)
        config_response.raise_for_status()
        
        with open(config_path, 'wb') as f:
            f.write(config_response.content)
            
        print(f"Config downloaded to {config_path}", file=sys.stderr)
        
        # Validate the files
        if os.path.exists(model_path) and os.path.getsize(model_path) > 0:
            if os.path.exists(config_path) and os.path.getsize(config_path) > 0:
                # Try to validate JSON config
                try:
                    with open(config_path, 'r') as f:
                        import json
                        config_data = json.load(f)
                        if "sample_rate" not in config_data:
                            print("Warning: Config file may be invalid (missing sample_rate)", file=sys.stderr)
                except Exception as e:
                    print(f"Warning: Config file may not be valid JSON: {e}", file=sys.stderr)
                
                print(f"Successfully downloaded Piper voice '{model_name}'", file=sys.stderr)
                return True
            else:
                print(f"Config file download failed or is empty", file=sys.stderr)
                return False
        else:
            print(f"Model file download failed or is empty", file=sys.stderr)
            return False
            
    except requests.RequestException as e:
        print(f"Error downloading files: {e}", file=sys.stderr)
        return False
    except Exception as e:
        print(f"Error processing files: {e}", file=sys.stderr)
        return False

# Example usage:
# download_piper_voice("en_GB-cori-high", "TTSmodels/")


def check_model_exists(model_name, model_dir):
    """
    Check if a model exists in the given directory.
    
    Args:
        model_name (str): Name of the model to check
        model_dir (str): Directory where model should be located
    
    Returns:
        bool: True if model exists, False otherwise
    """
    full_path = os.path.join(model_dir, model_name)
    return os.path.exists(full_path)

# Command-line interface for testing
if __name__ == "__main__":
    import argparse
    
    parser = argparse.ArgumentParser(description="Download and extract AI models")
    parser.add_argument("model_name", help="Name of the model to download")
    parser.add_argument("output_dir", help="Directory where model should be extracted")
    parser.add_argument("--url", help="Custom base URL for download")
    
    args = parser.parse_args()
    
    success = download_and_extract_model(
        args.model_name, 
        args.output_dir,
        base_url=args.url
    )
    
    if success:
        print(f"Model {args.model_name} successfully downloaded to {args.output_dir}")
    else:
        print(f"Failed to download model {args.model_name}")
        sys.exit(1)