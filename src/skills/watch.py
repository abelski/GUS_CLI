import pathlib, sys, time
from ui import print_info, print_user

def watch_folder(path_str: str, interval: int = 10) -> None:
    """Continuously check *path_str* every *interval* seconds.
    Prints 'quak' while the folder is empty and stops once a file appears.
    """
    folder = pathlib.Path(path_str)
    folder.mkdir(parents=True, exist_ok=True)
    while True:
        if any(folder.iterdir()):
            print_info('✅ file detected – stopping loop')
            break
        print_user('quak')
        sys.stdout.flush()
        time.sleep(interval)
