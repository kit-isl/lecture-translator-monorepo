


def set_display(page):
	# Change the display of the meeting room to display <page>
	# Possible values for page: home, controll, video_conference, lecture_translator, whiteboard
	print("Call set_display with parameter page:",page)
	return "forward"

def controll_zoom(command,link=""):
	# Controll zoom client to run the commands: start, join and end
	return "forward"


def controll_jitsi(command,link=""):
	# Controll jitsi client to run the commands: start, join and end
	return "forward"

def controll_lt(command, language="German",type="lecture"):
	#controll lecture translator
	#start meeting, start lecture, end, join and add_language
	print("Needs to be implemented")
	return ""

def keep_alive():
	return "forward"

def start_kitkat():
	return "forward"

