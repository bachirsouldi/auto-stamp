@echo off
echo Running Streamlit in Production mode (stamp.sophal.net)...
streamlit run index.py --browser.serverAddress stamp.sophal.net --browser.serverPort 443
pause
