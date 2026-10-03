import tkinter as tk

class CookieClicker:
    def __init__(self, master):
        self.master = master
        self.master.title("Cookie Clicker")

        self.cookie_count = 0

        self.cookie_label = tk.Label(master, text="Cookies: 0")
        self.cookie_label.pack()

        self.cookie_button = tk.Button(master, text="Click Me!", command=self.click_cookie)
        self.cookie_button.pack()

    def click_cookie(self):
        self.cookie_count += 1
        self.cookie_label.config(text="Cookies: {}".format(self.cookie_count))

if __name__ == "__main__":
    root = tk.Tk()
    game = CookieClicker(root)
    root.mainloop()
