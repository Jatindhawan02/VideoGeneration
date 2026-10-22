import phone, agent
from config import PKG
d = phone.connect()
phone.open_app(d, PKG["meesho"])
print(agent.run_task(d, "Open the Meesho home screen and stay there. Done when the Meesho home screen with products is visible.", max_steps=8, key="test_meesho"))
d.quit()
