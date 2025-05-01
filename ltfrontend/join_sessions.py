
from selenium import webdriver
from selenium.webdriver.chrome.options import Options
import time
from threading import Thread
from multiprocessing import Manager, Process
import sys

def start_browser(shared_dict, domain_port, sessionId):
    options = Options()
    options.add_argument("--headless")  # Run in headless mode
    options.add_argument("--disable-gpu")  # Recommended for headless mode stability
    options.add_argument("--window-size=1920,1080")  # Set window size

    # Set up the WebDriver (replace 'chromedriver' with your driver path if needed)
    driver = webdriver.Chrome(options=options)  # Or use webdriver.Firefox()

    # Open Google
    driver.get(f"https://{domain_port}/session/{sessionId}")

    """time.sleep(5)

    for i in range(2,5):
        button = driver.find_element(By.ID, f'{i}-button')
        button.click()
        time.sleep(1)"""

    while shared_dict["running"]:
        time.sleep(1)

    #print("DONE")
    #driver.get("https://example.com")
    #print("DONE2")

    # Close the browser
    driver.quit()

if __name__ == "__main__":
    if len(sys.argv) < 3:
        print("Usage: python join_sessions.py $domain_port $sessionId $numUsers\n  e.g. python join_sessions.py lecture-translator.kit.edu 300000 2")
        sys.exit()
    domain_port = sys.argv[1]
    sessionId = sys.argv[2]
    num = int(sys.argv[3])

    threads = False

    if threads:
        shared_dict = {"running": True}

        threads = [Thread(target=start_browser, args=(shared_dict,domain_port,sessionId)) for _ in range(num)]
        for t in threads:
            t.start()

        input("Press enter to exit: ")
        shared_dict["running"] = False

        for t in threads:
            t.join()
    else:
        with Manager() as manager:
            shared_dict = manager.dict()
            shared_dict["running"] = True

            processes = [Process(target=start_browser, args=(shared_dict,domain_port,sessionId)) for _ in range(num)]
            for p in processes:
                p.start()

            input("Press enter to exit: ")
            shared_dict["running"] = False

            for p in processes:
                p.join()
    
