import phone, find_product
d = phone.connect()
p, png = find_product.find_affiliate_product(d)
print("PRODUCT:", p)
d.quit()
