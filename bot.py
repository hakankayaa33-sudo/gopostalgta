import discord
from discord.ext import commands
import urllib.parse
import re
import asyncio
from flask import Flask
from threading import Thread

# Botu 7/24 aktif tutacak mini web sunucusu
app = Flask('')

@app.route('/')
def home():
    return "GoPostal Bot Aktif ve Calisiyor!"

def run_server():
    import os
    port = int(os.environ.get('PORT', 8080))
    app.run(host='0.0.0.0', port=port)

Thread(target=run_server).start()

intents = discord.Intents.default()
intents.message_content = True
intents.members = True # Botun kullanıcı ID'lerini bulabilmesi için üye yetkisi eklendi
bot = commands.Bot(command_prefix="!", intents=intents)

# Yalnızca Sunucu Yöneticileri veya 'saintvor_' kullanıcısına özel yetki kontrolü
def is_admin_or_saintvor():
    async def predicate(ctx):
        return ctx.author.name == "saintvor_" or ctx.author.guild_permissions.administrator
    return commands.check(predicate)

# Kullanıcıların maillerini hafızada tutacağımız basit bir sözlük
user_mails = {}

# Kargoları ve siparişleri tutacağımız sözlük ve takip no sayacı
orders = {}
order_counter = 1000

# Çalışanların maaş/bakiye bilgilerini tutacağımız sözlük
balances = {}

# Aktif mesaideki personeli ve giriş saatlerini tutacağımız sözlük
active_shifts = {}

# Harita linki oluşturmak için akıllı yardımcı fonksiyon (Modallardan önceye taşıdık)
def harita_linki_olustur(konum_metni: str) -> str:
    match = re.match(r"^\s*(-?\d+(?:\.\d+)?)\s*,\s*(-?\d+(?:\.\d+)?)\s*$", konum_metni)
    if match:
        x, y = match.groups()
        return f"https://map.gta.world/?x={x}&y={y}"
    else:
        return f"https://map.gta.world/?search={urllib.parse.quote(konum_metni)}"

# Şube Müdürlerine anında log/bildirim atan ortak yardımcı fonksiyon
async def yoneticilere_bildir(guild: discord.Guild, embed: discord.Embed):
    yonetici_rol = discord.utils.get(guild.roles, name="Şube Müdürü")
    if yonetici_rol:
        for uye in yonetici_rol.members:
            if not uye.bot:
                try:
                    await uye.send(embed=embed)
                except discord.Forbidden:
                    pass

class MailMesajModal(discord.ui.Modal):
    def __init__(self, hedef: discord.Member):
        super().__init__(title=f"Mail Gönder: {hedef.display_name}"[:45])
        self.hedef_uye = hedef

    gonderen = discord.ui.TextInput(label="Kimden (Adınız/Departman)", placeholder="Örn: GoPostal İK", max_length=50)
    mesaj = discord.ui.TextInput(label="Mail / Mesaj İçeriği", style=discord.TextStyle.paragraph, placeholder="Göndermek istediğiniz maili buraya yazın...")
    foto = discord.ui.TextInput(label="Fotoğraf/Ek Linki (İsteğe Bağlı)", placeholder="Örn: https://i.imgur.com/...", required=False)

    async def on_submit(self, interaction: discord.Interaction):
        uye = self.hedef_uye
        if uye.id not in user_mails:
            user_mails[uye.id] = []
        
        sirket_maili = f"{interaction.user.display_name.lower().replace(' ', '.')}@gopostal.com"
        tam_gonderen = f"{self.gonderen.value} <{sirket_maili}>"
        
        ek_link = self.foto.value.strip() if self.foto.value else ""
        user_mails[uye.id].append({'gonderen': tam_gonderen, 'mesaj': self.mesaj.value, 'foto': ek_link})
        
        # Özelden Şık Tasarımlı DM
        try:
            embed = discord.Embed(title="🏢 GoPostal Şirket Maili", description=self.mesaj.value, color=discord.Color.blue())
            embed.set_author(name=f"Kimden: {tam_gonderen}")
            if ek_link.startswith("http"):
                embed.set_image(url=ek_link)
            embed.set_thumbnail(url="https://i.imgur.com/OXvf62G.png") # GoPostal Logosu
            embed.set_footer(text="GoPostal İletişim Sistemleri")
            await uye.send(embed=embed)
        except discord.Forbidden:
            pass
        
        await interaction.response.send_message(f"✅ Mail {uye.mention} adlı çalışana başarıyla iletildi!", ephemeral=True)

class MailHedefSecView(discord.ui.View):
    def __init__(self):
        super().__init__(timeout=180) # Kullanıcının 3 dakika içinde seçim yapması gerekir

    @discord.ui.select(cls=discord.ui.UserSelect, placeholder="Mail gönderilecek kişiyi seçin...")
    async def select_user(self, interaction: discord.Interaction, select: discord.ui.UserSelect):
        hedef_uye = select.values[0]
        await interaction.response.send_modal(MailMesajModal(hedef=hedef_uye))

class TopluMailModal(discord.ui.Modal, title="Toplu Mail Gönder"):
    gonderen = discord.ui.TextInput(label="Kimden (Adınız/Departman)", placeholder="Örn: Merkez Ofis", max_length=50)
    mesaj = discord.ui.TextInput(label="Mail / Mesaj İçeriği", style=discord.TextStyle.paragraph, placeholder="Tüm çalışanlara iletilecek mesajı yazın...")
    foto = discord.ui.TextInput(label="Fotoğraf/Ek Linki (İsteğe Bağlı)", placeholder="Örn: https://i.imgur.com/...", required=False)

    async def on_submit(self, interaction: discord.Interaction):
        # Toplu mail gönderimi uzun sürebileceği için etkileşimi beklemeye alıyoruz
        await interaction.response.defer(ephemeral=True)
        
        sirket_maili = f"{interaction.user.display_name.lower().replace(' ', '.')}@gopostal.com"
        tam_gonderen = f"{self.gonderen.value} <{sirket_maili}>"
        ek_link = self.foto.value.strip() if self.foto.value else ""
        
        gonderilen_kisi = 0
        calisan_rolleri = ["Şube Müdürü", "Lojistik Uzmanı", "Kargo Aracı Şoförü", "Motorlu Kurye"]
        
        for uye in interaction.guild.members:
            if uye.bot: 
                continue
            uye_rolleri = [r.name for r in uye.roles]
            
            # Legal FM ve Müşteri hariç, sadece çalışan rollerine sahip olanlara gönder
            if any(r in calisan_rolleri for r in uye_rolleri) and not ("LEGAL FM" in uye_rolleri or "Müşteri" in uye_rolleri):
                if uye.id not in user_mails:
                    user_mails[uye.id] = []
                
                user_mails[uye.id].append({'gonderen': tam_gonderen, 'mesaj': self.mesaj.value, 'foto': ek_link})
                gonderilen_kisi += 1
                
                try:
                    embed = discord.Embed(title="📢 GoPostal Şirket Duyurusu (Toplu Mail)", description=self.mesaj.value, color=discord.Color.red())
                    embed.set_author(name=f"Kimden: {tam_gonderen}")
                    if ek_link.startswith("http"):
                        embed.set_image(url=ek_link)
                    embed.set_thumbnail(url="https://i.imgur.com/OXvf62G.png")
                    embed.set_footer(text="GoPostal İletişim Sistemleri")
                    await uye.send(embed=embed)
                except discord.Forbidden:
                    pass
                    
        await interaction.followup.send(f"✅ Toplu mail başarıyla **{gonderilen_kisi}** çalışana iletildi!", ephemeral=True)

class SiparisOlusturModal(discord.ui.Modal, title="Sipariş Oluştur"):
    isim = discord.ui.TextInput(label="Müşteri Adı Soyadı", placeholder="Örn: John Doe", max_length=50)
    bolge = discord.ui.TextInput(label="Koordinat (jackymap.vercel.app)", placeholder="Haritadan bulduğunuz X,Y koordinatını girin")
    icerik = discord.ui.TextInput(label="Paket İçeriği", style=discord.TextStyle.paragraph)

    async def on_submit(self, interaction: discord.Interaction):
        global order_counter
        order_counter += 1
        kargo_id = f"GP-{order_counter}"
        
        orders[kargo_id] = {
            "olusturan": interaction.user.display_name,
            "musteri_isim": self.isim.value,
            "musteri_id": interaction.user.id,
            "hedef_bolge": self.bolge.value,
            "icerik": self.icerik.value,
            "durum": "Paketleniyor",
            "konum": "GoPostal Merkez Depo"
        }
        
        # Yöneticilere anında kargo açılış DM'i gönderir
        embed_notif = discord.Embed(title="📦 Yeni Kargo Talebi", description=f"👤 **Müşteri:** {interaction.user.mention} ({self.isim.value})\n🔖 **Kargo ID:** `{kargo_id}`\n📍 **Hedef:** {self.bolge.value}\n📦 **İçerik:** {self.icerik.value}", color=discord.Color.blue())
        embed_notif.set_thumbnail(url="https://i.imgur.com/OXvf62G.png")
        await yoneticilere_bildir(interaction.guild, embed_notif)

        # Sadece Müşterinin ve Yetkililerin Görebileceği Özel Kargo Kanalı Kurulumu
        kategori = discord.utils.get(interaction.guild.categories, name="📦 KARGO TALEPLERİ")
        kategori = discord.utils.get(interaction.guild.categories, name="📦 GENEL KARGO İSTEKLERİ")
        if not kategori:
            kategori = await interaction.guild.create_category("📦 KARGO TALEPLERİ")
            kategori = await interaction.guild.create_category("📦 GENEL KARGO İSTEKLERİ")
            
        overwrites = {
            interaction.guild.default_role: discord.PermissionOverwrite(read_messages=False),
            interaction.user: discord.PermissionOverwrite(read_messages=True, send_messages=True)
        }
        for r_name in ["Şube Müdürü", "Lojistik Uzmanı", "Kargo Aracı Şoförü", "Motorlu Kurye", "LEGAL FM"]:
            r = discord.utils.get(interaction.guild.roles, name=r_name)
            if r:
                overwrites[r] = discord.PermissionOverwrite(read_messages=True, send_messages=True)
                
        kanal_adi = f"kargo-{kargo_id.lower()}"
        kargo_kanal = await interaction.guild.create_text_channel(kanal_adi, category=kategori, overwrites=overwrites)
        
        embed_kanal = discord.Embed(title=f"📦 {kargo_id} Numaralı Kargo Talebi", color=discord.Color.gold())
        embed_kanal.set_thumbnail(url="https://i.imgur.com/OXvf62G.png")
        embed_kanal.add_field(name="Müşteri", value=f"{interaction.user.mention} - {self.isim.value}", inline=False)
        embed_kanal.add_field(name="İçerik", value=self.icerik.value, inline=False)
        embed_kanal.add_field(name="Hedef Bölge", value=self.bolge.value, inline=False)
        await kargo_kanal.send(f"{interaction.user.mention} Kargonuz oluşturuldu! Süreci buradan takip edebilir, yetkililerle iletişime geçebilirsiniz.", embed=embed_kanal, view=KargoKanalView())
        await interaction.response.send_message(f"✅ Yeni kargo talebi oluşturuldu!\n**Kargo ID:** `{kargo_id}`\n**Özel Kargo Kanalınız:** {kargo_kanal.mention}", ephemeral=True)

class SiparisGuncelleModal(discord.ui.Modal, title="Sipariş Güncelle"):
    kargo_id = discord.ui.TextInput(label="Kargo ID (Örn: GP-1001)")
    konum = discord.ui.TextInput(label="Yeni Konum (jackymap.vercel.app)", placeholder="Örn: 250,-1000")
    durum = discord.ui.TextInput(label="Kargo Durumu", placeholder="Örn: Yolda, Teslim Edildi")

    async def on_submit(self, interaction: discord.Interaction):
        # Yetki Kontrolü
        roller = [r.name for r in interaction.user.roles]
        yetkili = ["Şube Müdürü", "Lojistik Uzmanı", "Kargo Aracı Şoförü", "Motorlu Kurye", "LEGAL FM"]
        if not any(r in yetkili for r in roller) and not interaction.user.guild_permissions.administrator and interaction.user.name != "saintvor_":
            await interaction.response.send_message("❌ Bu işlem için şirket yetkiniz yok.", ephemeral=True)
            return
            
        kid = self.kargo_id.value.strip()
        if kid not in orders:
            await interaction.response.send_message("❌ Belirtilen kargo bulunamadı.", ephemeral=True)
            return
        
        orders[kid]["konum"] = self.konum.value
        orders[kid]["durum"] = self.durum.value
        konum_url = harita_linki_olustur(self.konum.value)
        
        ek_mesaj = ""
        # Eğer kargo durumu olarak "teslim" kelimesi girilirse kuryeye otomatik maaş öder
        if "teslim" in self.durum.value.lower():
            uid = interaction.user.id
            balances[uid] = balances.get(uid, 0) + 500
            ek_mesaj = f"\n\n💸 **Maaş Ödemesi:** Kargo başarıyla teslim edildiği için hesabınıza **$500** bonus yatırıldı!"
            
            # Yöneticilere Teslimat DM'i gönderir
            embed_notif = discord.Embed(title="📦 Kargo Teslim Edildi", description=f"👤 **Kurye:** {interaction.user.mention}\n🔖 **Kargo ID:** `{kid}`\n📍 **Son Konum:** {self.konum.value}", color=discord.Color.green())
            embed_notif.set_thumbnail(url="https://i.imgur.com/OXvf62G.png")
            await yoneticilere_bildir(interaction.guild, embed_notif)
            
            # Kargo kanalını "ARŞİVLENDİ" olarak günceller ve müşteriden gizler
            kanal_adi = f"kargo-{kid.lower()}"
            kargo_kanal = discord.utils.get(interaction.guild.text_channels, name=kanal_adi)
            if kargo_kanal:
                arsiv_kat = discord.utils.get(interaction.guild.categories, name="📁 ARŞİVLENEN KARGOLAR")
                if not arsiv_kat:
                    arsiv_kat = await interaction.guild.create_category("📁 ARŞİVLENEN KARGOLAR")
                
                yeni_overwrites = {interaction.guild.default_role: discord.PermissionOverwrite(read_messages=False)}
                for r_name in ["Şube Müdürü", "Lojistik Uzmanı", "Kargo Aracı Şoförü", "Motorlu Kurye", "LEGAL FM"]:
                    r = discord.utils.get(interaction.guild.roles, name=r_name)
                    if r:
                        yeni_overwrites[r] = discord.PermissionOverwrite(read_messages=True, send_messages=False)
                
                await kargo_kanal.edit(category=arsiv_kat, overwrites=yeni_overwrites)
                await kargo_kanal.send("📦 **Bu kargo teslim edilmiş ve kanal arşive kaldırılmıştır.**")
            
        await interaction.response.send_message(f"✅ `{kid}` numaralı kargo güncellendi!\n📍 **Yeni Konum:** {self.konum.value}\n🚚 **Durum:** {self.durum.value}{ek_mesaj}", ephemeral=True)

class SiparisTakipModal(discord.ui.Modal, title="Kargo Takip"):
    kargo_id = discord.ui.TextInput(label="Kargo ID (Örn: GP-1001)")

    async def on_submit(self, interaction: discord.Interaction):
        kid = self.kargo_id.value.strip()
        if kid not in orders:
            await interaction.response.send_message("❌ Kargo bulunamadı. ID'yi kontrol edin.", ephemeral=True)
            return
        
        kargo = orders[kid]
        bolge_url = harita_linki_olustur(kargo['hedef_bolge'])
        konum_url = harita_linki_olustur(kargo['konum'])

        embed = discord.Embed(title=f"📦 Kargo Takip: {kid}", color=discord.Color.gold())
        embed.set_thumbnail(url="https://i.imgur.com/OXvf62G.png")
        embed.add_field(name="Paket İçeriği", value=kargo["icerik"], inline=False)
        embed.add_field(name="Hedef Teslimat Bölgesi", value=f"[{kargo['hedef_bolge']}]({bolge_url})", inline=True)
        embed.add_field(name="Anlık Harita Konumu", value=f"📍 [{kargo['konum']}]({konum_url})", inline=True)
        embed.add_field(name="Son Durum", value=f"🚚 {kargo['durum']}", inline=False)
        embed.set_footer(text=f"Talep Eden: {kargo['olusturan']}")
        
        await interaction.response.send_message(embed=embed, ephemeral=True)

class DestekModal(discord.ui.Modal, title="Müşteri Destek Talebi"):
    konu = discord.ui.TextInput(label="Konu", placeholder="Örn: Kargo Gecikmesi, Şikayet", max_length=50)
    mesaj = discord.ui.TextInput(label="Detaylı Mesajınız", style=discord.TextStyle.paragraph)

    async def on_submit(self, interaction: discord.Interaction):
        guild = interaction.guild
        kategori = discord.utils.get(guild.categories, name="📩 GELEN MAILLER")
        if not kategori:
            kategori = await guild.create_category("📩 GELEN MAILLER")
            
        overwrites = {
            guild.default_role: discord.PermissionOverwrite(read_messages=False),
            interaction.user: discord.PermissionOverwrite(read_messages=True, send_messages=True)
        }
        # Yetkililerin destek talebini görebilmesi için izinler
        for r_name in ["Şube Müdürü", "Lojistik Uzmanı", "Kargo Aracı Şoförü", "Motorlu Kurye", "LEGAL FM"]:
            r = discord.utils.get(guild.roles, name=r_name)
            if r:
                overwrites[r] = discord.PermissionOverwrite(read_messages=True, send_messages=True)
                
        kanal_adi = f"destek-{interaction.user.name}"
        destek_kanal = await guild.create_text_channel(kanal_adi, category=kategori, overwrites=overwrites)
        
        embed = discord.Embed(title="🛠️ Müşteri Destek Talebi", color=discord.Color.red())
        embed.set_thumbnail(url="https://i.imgur.com/OXvf62G.png")
        embed.add_field(name="Müşteri", value=interaction.user.mention, inline=False)
        embed.add_field(name="Konu", value=self.konu.value, inline=False)
        embed.add_field(name="Mesaj", value=self.mesaj.value, inline=False)
        
        await destek_kanal.send(f"{interaction.user.mention} Destek talebiniz alındı, yetkililer kısa süre içerisinde sizinle iletişime geçecektir.", embed=embed, view=KargoKanalView())
        await interaction.response.send_message(f"✅ Destek talebiniz başarıyla oluşturuldu: {destek_kanal.mention}", ephemeral=True)

class DestekView(discord.ui.View):
    def __init__(self):
        super().__init__(timeout=None)
    @discord.ui.button(label="🆘 Destek Talebi Aç", style=discord.ButtonStyle.danger, custom_id="destek_talep_btn")
    async def btn_destek(self, interaction: discord.Interaction, button: discord.ui.Button):
        await interaction.response.send_modal(DestekModal())

class MesaiView(discord.ui.View):
    def __init__(self):
        super().__init__(timeout=None)
        
    async def update_mesai_embed(self, interaction: discord.Interaction):
        embed = discord.Embed(title="⏰ Mesai Takip", description="Aşağıdaki butonları kullanarak mesai giriş ve çıkışlarınızı yapabilirsiniz.", color=discord.Color.blue())
        embed.set_thumbnail(url="https://i.imgur.com/OXvf62G.png")
        if active_shifts:
            lines = [f"• <@{uid}> - Giriş: <t:{ts}:t> (<t:{ts}:R>)" for uid, ts in active_shifts.items()]
            embed.add_field(name="🟢 Aktif Mesaidekiler", value="\n".join(lines), inline=False)
        else:
            embed.add_field(name="🟢 Aktif Mesaidekiler", value="Şu an mesaide kimse yok.", inline=False)
        await interaction.message.edit(embed=embed)

    @discord.ui.button(label="🟢 Mesaiye Başla", style=discord.ButtonStyle.success, custom_id="mesai_basla_btn")
    async def btn_basla(self, interaction: discord.Interaction, button: discord.ui.Button):
        uid = interaction.user.id
        if uid in active_shifts:
            await interaction.response.send_message("❌ Zaten aktif olarak mesaiye giriş yapmış durumdasınız!", ephemeral=True)
            return
            
        ts = int(discord.utils.utcnow().timestamp())
        active_shifts[uid] = ts
        
        await interaction.response.defer(ephemeral=True)
        await self.update_mesai_embed(interaction)
        
        embed = discord.Embed(title="🏢 GoPostal Mesai Bildirimi", description=f"👤 **Personel:** {interaction.user.mention}\n🟢 **Durum:** Mesaiye BAŞLADI.\n⏰ **Zaman:** <t:{ts}:F>", color=discord.Color.green())
        embed.set_thumbnail(url="https://i.imgur.com/OXvf62G.png")
        await yoneticilere_bildir(interaction.guild, embed)
        
        await interaction.channel.send(f"🟢 {interaction.user.mention} mesaiye **giriş** yaptı. İyi çalışmalar!", delete_after=10)
        await interaction.followup.send("✅ Mesai girişiniz kaydedildi ve yöneticilere bildirildi.", ephemeral=True)

    @discord.ui.button(label="🔴 Mesaiden Çık", style=discord.ButtonStyle.danger, custom_id="mesai_bitir_btn")
    async def btn_bitir(self, interaction: discord.Interaction, button: discord.ui.Button):
        uid = interaction.user.id
        if uid not in active_shifts:
            await interaction.response.send_message("❌ Şu an aktif bir mesaide görünmüyorsunuz!", ephemeral=True)
            return
            
        del active_shifts[uid]
        ts = int(discord.utils.utcnow().timestamp())
        
        await interaction.response.defer(ephemeral=True)
        await self.update_mesai_embed(interaction)
        
        embed = discord.Embed(title="🏢 GoPostal Mesai Bildirimi", description=f"👤 **Personel:** {interaction.user.mention}\n🔴 **Durum:** Mesaiden ÇIKIŞ YAPTI.\n⏰ **Zaman:** <t:{ts}:F>", color=discord.Color.red())
        embed.set_thumbnail(url="https://i.imgur.com/OXvf62G.png")
        await yoneticilere_bildir(interaction.guild, embed)
        
        await interaction.channel.send(f"🔴 {interaction.user.mention} mesaiden **çıkış** yaptı. İyi dinlenmeler!", delete_after=10)
        await interaction.followup.send("✅ Mesai çıkışınız kaydedildi ve yöneticilere bildirildi.", ephemeral=True)

class AracRaporModal(discord.ui.Modal, title="Araç Durum Raporu"):
    arac = discord.ui.TextInput(label="Araç Plakası veya Modeli", placeholder="Örn: GoPostal Kamyon / 34 GP 01")
    durum = discord.ui.TextInput(label="Araç Durumu / Hasar Bilgisi", style=discord.TextStyle.paragraph, placeholder="Örn: Sol far kırık, yakıt %20")
    async def on_submit(self, interaction: discord.Interaction):
        embed = discord.Embed(title="🚐 Araç Durum Raporu", color=discord.Color.orange())
        embed.set_thumbnail(url="https://i.imgur.com/OXvf62G.png")
        embed.add_field(name="Rapor Eden", value=interaction.user.mention, inline=False)
        embed.add_field(name="Araç", value=self.arac.value, inline=False)
        embed.add_field(name="Durum/Hasar", value=self.durum.value, inline=False)
        await interaction.channel.send(embed=embed, delete_after=15)
        await interaction.response.send_message("✅ Raporunuz başarıyla iletildi.", ephemeral=True)

class AracRaporView(discord.ui.View):
    def __init__(self):
        super().__init__(timeout=None)
    @discord.ui.button(label="🚐 Araç Raporla", style=discord.ButtonStyle.danger, custom_id="arac_rapor_btn")
    async def btn_rapor(self, interaction: discord.Interaction, button: discord.ui.Button):
        await interaction.response.send_modal(AracRaporModal())

class TicketModal(discord.ui.Modal, title="GoPostal İletişim Formu"):
    isim = discord.ui.TextInput(label="İsim Soyisim (Kimden)", placeholder="Örn: John Doe", max_length=50)
    telefon = discord.ui.TextInput(label="Telefon Numarası", placeholder="Örn: 555-0192", max_length=20)
    adres = discord.ui.TextInput(label="Konum/Adres (jackymap.vercel.app)", style=discord.TextStyle.short, placeholder="Haritadan bulduğunuz X,Y koordinatını girin", max_length=200, required=False)
    mesaj = discord.ui.TextInput(label="Mail / Mesaj İçeriği", style=discord.TextStyle.paragraph, placeholder="Bize ne iletmek istiyorsunuz?", max_length=1000)
    foto = discord.ui.TextInput(label="Fotoğraf/Ek Linki (İsteğe Bağlı)", placeholder="Örn: https://i.imgur.com/...", required=False)

    async def on_submit(self, interaction: discord.Interaction):
        guild = interaction.guild
        # Biletlerin (Ticket) açılacağı kategoriyi kontrol et, yoksa oluştur
        kategori = discord.utils.get(guild.categories, name="📩 GELEN MAILLER")
        kategori = discord.utils.get(guild.categories, name="📦 GENEL KARGO İSTEKLERİ")
        if not kategori:
            kategori = await guild.create_category("📩 GELEN MAILLER")
            kategori = await guild.create_category("📦 GENEL KARGO İSTEKLERİ")
        
        # Kanal izinlerini ayarla (Sadece bileti açan kişi ve yöneticiler görebilir)
        overwrites = {
            guild.default_role: discord.PermissionOverwrite(read_messages=False),
            interaction.user: discord.PermissionOverwrite(read_messages=True, send_messages=True)
        }
        # Yetkililerin biletleri görebilmesi için izinler
        for r_name in ["Şube Müdürü", "Lojistik Uzmanı", "Kargo Aracı Şoförü", "Motorlu Kurye", "LEGAL FM"]:
            r = discord.utils.get(guild.roles, name=r_name)
            if r:
                overwrites[r] = discord.PermissionOverwrite(read_messages=True, send_messages=True)
        
        # Özel kanalı oluştur
        kanal_adi = f"bilet-{interaction.user.name}"
        ticket_kanal = await guild.create_text_channel(kanal_adi, category=kategori, overwrites=overwrites)
        
        # Girilen bilgileri kanala gönder
        embed = discord.Embed(title="📩 Yeni Bilet / Mail", color=discord.Color.green())
        embed.set_thumbnail(url="https://i.imgur.com/OXvf62G.png")
        embed.add_field(name="Gönderen", value=interaction.user.mention, inline=False)
        embed.add_field(name="İsim", value=self.isim.value, inline=True)
        embed.add_field(name="Telefon", value=self.telefon.value, inline=True)
        embed.add_field(name="Adres", value=self.adres.value if self.adres.value else "Belirtilmedi", inline=False)
        embed.add_field(name="Mesaj / Mail", value=self.mesaj.value, inline=False)
        
        if self.foto.value and self.foto.value.startswith("http"):
            embed.set_image(url=self.foto.value)
        
        await ticket_kanal.send(f"{interaction.user.mention} Talebiniz alındı, yetkililer yakında dönüş yapacaktır.", embed=embed, view=KargoKanalView())
        await interaction.response.send_message(f"✅ Biletiniz oluşturuldu: {ticket_kanal.mention}", ephemeral=True)

class TicketView(discord.ui.View):
    def __init__(self):
        super().__init__(timeout=None) # Butonun süresiz olarak kalması için timeout=None
        # Müşterilerin koordinat bulabilmesi için tıklandığında haritayı açan buton
        self.add_item(discord.ui.Button(label="🗺️ GTA Haritasını Aç", url="https://jackymap.vercel.app/", row=0))

    @discord.ui.button(label="📩 Bilet / Kargo Talebi", style=discord.ButtonStyle.success, custom_id="ticket_olustur_btn", row=1)
    async def ticket_olustur(self, interaction: discord.Interaction, button: discord.ui.Button):
        # Butona tıklandığında formu ekrana getir
        await interaction.response.send_modal(TicketModal())

class MailView(discord.ui.View):
    def __init__(self):
        super().__init__(timeout=None)
    
    @discord.ui.button(label="📧 Mail Gönder", style=discord.ButtonStyle.success, custom_id="mail_gonder_btn")
    async def btn_gonder(self, interaction: discord.Interaction, button: discord.ui.Button):
        try:
            await interaction.response.send_message("Kime mail göndermek istersiniz?", view=MailHedefSecView(), ephemeral=True)
        except discord.HTTPException:
            pass
        
    @discord.ui.button(label="📥 Kutuyu Oku", style=discord.ButtonStyle.primary, custom_id="mail_oku_btn")
    async def btn_oku(self, interaction: discord.Interaction, button: discord.ui.Button):
        try:
            # Etkileşimi hızlıca onaylayıp "Unknown interaction (10062)" hatasını önleriz
            await interaction.response.defer(ephemeral=True)
            
            mailler = user_mails.get(interaction.user.id, [])
            if not mailler:
                await interaction.followup.send("📭 Gelen kutunuz şu an boş.", ephemeral=True)
                return
                
            embed = discord.Embed(title=f"📧 {interaction.user.display_name} - Gelen Kutusu", color=discord.Color.green())
            embed.set_thumbnail(url="https://i.imgur.com/OXvf62G.png")
            for i, m in enumerate(mailler, 1):
                icerik = m['mesaj']
                if m.get('foto') and m['foto'].startswith("http"):
                    icerik += f"\n\n📎 **Ek:** [Fotoğrafı Görüntüle]({m['foto']})"
                embed.add_field(name=f"Mail #{i} | Kimden: {m['gonderen']}", value=icerik, inline=False)
            
            try:
                await interaction.user.send(embed=embed)
                await interaction.followup.send("✅ Mailleriniz size özel mesaj (DM) olarak gönderildi. Lütfen özel mesaj kutunuzu kontrol edin.", ephemeral=True)
            except discord.Forbidden:
                await interaction.followup.send("❌ Size özel mesaj gönderemiyorum. Lütfen sunucu gizlilik ayarlarından 'Sunucu üyelerinden gelen doğrudan mesajlara izin ver' seçeneğini açın.", ephemeral=True)
        except discord.HTTPException:
            pass

    @discord.ui.button(label="📢 Toplu Mail", style=discord.ButtonStyle.danger, custom_id="mail_toplu_btn")
    async def btn_toplu(self, interaction: discord.Interaction, button: discord.ui.Button):
        await interaction.response.send_modal(TopluMailModal())

class SiparisGorevliView(discord.ui.View):
    def __init__(self):
        super().__init__(timeout=None)
        # Çalışanların kolayca koordinat bulabilmesi için tıklandığında haritayı açan buton
        self.add_item(discord.ui.Button(label="🗺️ GTA Haritasını Aç", url="https://jackymap.vercel.app/", row=0))
        
    @discord.ui.button(label="📝 Yeni Kargo Kaydı", style=discord.ButtonStyle.success, custom_id="siparis_olustur_btn", row=1)
    async def btn_olustur(self, interaction: discord.Interaction, button: discord.ui.Button):
        await interaction.response.send_modal(SiparisOlusturModal())
    @discord.ui.button(label="🔄 Kargo Konum Güncelle", style=discord.ButtonStyle.primary, custom_id="siparis_guncelle_btn", row=1)
    async def btn_guncelle(self, interaction: discord.Interaction, button: discord.ui.Button):
        await interaction.response.send_modal(SiparisGuncelleModal())

class SiparisMusteriView(discord.ui.View):
    def __init__(self):
        super().__init__(timeout=None)
        self.add_item(discord.ui.Button(label="🗺️ GTA Haritasını Aç", url="https://jackymap.vercel.app/", row=0))
        
    @discord.ui.button(label="🔍 Kargo Nerede?", style=discord.ButtonStyle.primary, custom_id="siparis_takip_btn", row=1)
    async def btn_takip(self, interaction: discord.Interaction, button: discord.ui.Button):
        await interaction.response.send_modal(SiparisTakipModal())

class KargoFaturaModal(discord.ui.Modal, title="Teslimat & Makbuz Kes"):
    tarih = discord.ui.TextInput(label="Tarih", placeholder="Örn: 25.05.2026", max_length=20)
    hizmet = discord.ui.TextInput(label="Hizmet / Açıklama", placeholder="Örn: Kargo Teslimatı", max_length=100)
    fiyat = discord.ui.TextInput(label="Tutar / Fiyat", placeholder="Örn: $500", max_length=20)
    foto = discord.ui.TextInput(label="Teslimat Fotoğrafı (URL)", required=False)

    async def on_submit(self, interaction: discord.Interaction):
        kanal_adi = interaction.channel.name
        kid = kanal_adi.replace("kargo-", "").replace("bilet-", "").replace("destek-", "").upper()
        
        if kid in orders:
            orders[kid]["durum"] = "Teslim Edildi / Sonlandırıldı"
            
        musteri_mention = "Müşteri"
        # Formun gönderildiği kanaldaki ilk mesajın embed'inden Müşteri Etiketini otomatik bulur
        if interaction.message and interaction.message.embeds:
            try:
                musteri_mention = interaction.message.embeds[0].fields[0].value
            except:
                pass

        embed = discord.Embed(title="🧾 Teslimat Makbuzu / Fatura", color=discord.Color.green())
        embed.set_thumbnail(url="https://i.imgur.com/OXvf62G.png")
        embed.add_field(name="Müşteri", value=musteri_mention, inline=True)
        embed.add_field(name="Tarih", value=self.tarih.value, inline=True)
        embed.add_field(name="Tutar", value=self.fiyat.value, inline=False)
        embed.add_field(name="Açıklama", value=self.hizmet.value, inline=False)
        
        if self.foto.value and self.foto.value.startswith("http"):
            embed.set_image(url=self.foto.value)
            
        embed.set_footer(text="Bizi Tercih Ettiğiniz İçin Teşekkürler! | GoPostal Teslimat")
        
        await interaction.channel.send(f"🔔 {musteri_mention}, faturanız başarıyla oluşturuldu!", embed=embed)
        
        # Müşteriye ve Yöneticilere DM ilet
        match = re.search(r'<@!?(\d+)>', musteri_mention)
        if match:
            hedef_uye = interaction.guild.get_member(int(match.group(1)))
            if hedef_uye:
                try:
                    await hedef_uye.send("🧾 **GoPostal:** Yeni bir teslimat faturanız var!", embed=embed)
                except discord.Forbidden:
                    pass
        
        await yoneticilere_bildir(interaction.guild, embed)
        await interaction.response.send_message("✅ Fatura başarıyla kesildi. Müşteriye ve Yöneticilere iletildi.", ephemeral=True)

class KargoKanalView(discord.ui.View):
    def __init__(self):
        super().__init__(timeout=None)
        
    async def check_perms(self, interaction: discord.Interaction):
        roller = [r.name for r in interaction.user.roles]
        yetkili = ["Şube Müdürü", "Lojistik Uzmanı", "Kargo Aracı Şoförü", "Motorlu Kurye", "LEGAL FM"]
        if not any(r in yetkili for r in roller) and not interaction.user.guild_permissions.administrator and interaction.user.name != "saintvor_":
            await interaction.response.send_message("❌ Bu işlem için şirket yetkiniz yok.", ephemeral=True)
            return False
        return True

    @discord.ui.button(label="✅ Kabul Et", style=discord.ButtonStyle.success, custom_id="kargo_onayla_btn")
    async def btn_onayla(self, interaction: discord.Interaction, button: discord.ui.Button):
        if not await self.check_perms(interaction): return
        kid = interaction.channel.name.replace("kargo-", "").upper()
        if kid in orders: orders[kid]["durum"] = "Onaylandı / İşlemde"
        
        # Kanal isminin başına "onay-" ekler
        yeni_isim = f"onay-{interaction.channel.name}"
        if not interaction.channel.name.startswith("onay-"):
            try:
                await interaction.channel.edit(name=yeni_isim)
            except:
                pass
                
        # Lojistik ekibine (Aktif Kargolar kanalına) işi düşürür
        aktif_kanal = discord.utils.get(interaction.guild.text_channels, name="aktif-kargolar")
        if aktif_kanal and kid in orders:
            kargo = orders[kid]
            bolge_url = harita_linki_olustur(kargo.get('hedef_bolge', 'Los Santos'))
            
            embed = discord.Embed(
                title="🚀 Yeni Kargo Teslimatı Bekleniyor!", 
                description=f"{interaction.user.mention} tarafından bir kargo talebi onaylandı. Kuryeler teslimata başlayabilir.", 
                color=discord.Color.green()
            )
            embed.set_thumbnail(url="https://i.imgur.com/OXvf62G.png")
            embed.add_field(name="🔖 Kargo ID", value=f"`{kid}`", inline=True)
            embed.add_field(name="📍 Teslimat Bölgesi", value=f"{kargo.get('hedef_bolge', 'Bilinmiyor')}", inline=True)
            embed.add_field(name="📦 Paket İçeriği", value=kargo.get('icerik', 'Bilinmiyor'), inline=False)
            
            musteri_isim = kargo.get('musteri_isim', kargo.get('olusturan', 'Bilinmiyor'))
            embed.set_footer(text=f"Müşteri: {musteri_isim}")
            
            kurye_rol = discord.utils.get(interaction.guild.roles, name="Motorlu Kurye")
            sofor_rol = discord.utils.get(interaction.guild.roles, name="Kargo Aracı Şoförü")
            mesaj_metni = "🔔 Yeni kargo işi hazır!"
            if kurye_rol and sofor_rol:
                mesaj_metni = f"🔔 {kurye_rol.mention} {sofor_rol.mention} Yeni kargo işi hazır!"
            
            await aktif_kanal.send(content=mesaj_metni, embed=embed)

        await interaction.response.send_message("✅ Talep yetkililer tarafından **kabul edildi.**")

    @discord.ui.button(label="❌ Reddet", style=discord.ButtonStyle.danger, custom_id="kargo_reddet_btn")
    async def btn_reddet(self, interaction: discord.Interaction, button: discord.ui.Button):
        if not await self.check_perms(interaction): return
        kid = interaction.channel.name.replace("kargo-", "").upper()
        if kid in orders: orders[kid]["durum"] = "Reddedildi"
        await interaction.response.send_message("❌ Talep yetkililer tarafından **reddedildi**.")
        await interaction.response.send_message("❌ Talep reddedildi. Kanal 3 saniye içinde tamamen siliniyor...")
        await asyncio.sleep(3)
        try:
            await interaction.channel.delete(reason="Kargo talebi yetkili tarafından reddedildi.")
        except:
            pass

    @discord.ui.button(label="🧾 Fatura Kes", style=discord.ButtonStyle.primary, custom_id="kargo_fatura_btn_yeni")
    async def btn_fatura(self, interaction: discord.Interaction, button: discord.ui.Button):
        if not await self.check_perms(interaction): return
        await interaction.response.send_modal(KargoFaturaModal())

    @discord.ui.button(label="📦 Arşive Kaldır", style=discord.ButtonStyle.secondary, custom_id="bilet_kapat_btn")
    async def btn_kapat(self, interaction: discord.Interaction, button: discord.ui.Button):
        if not await self.check_perms(interaction): return
        
        arsiv_kat = discord.utils.get(interaction.guild.categories, name="📁 ARŞİVLENEN KARGOLAR")
        if not arsiv_kat:
            arsiv_kat = await interaction.guild.create_category("📁 ARŞİVLENEN KARGOLAR")
        
        yeni_overwrites = {interaction.guild.default_role: discord.PermissionOverwrite(read_messages=False)}
        for r_name in ["Şube Müdürü", "Lojistik Uzmanı", "Kargo Aracı Şoförü", "Motorlu Kurye", "LEGAL FM"]:
            r = discord.utils.get(interaction.guild.roles, name=r_name)
            if r: yeni_overwrites[r] = discord.PermissionOverwrite(read_messages=True, send_messages=False)
        
        await interaction.channel.edit(category=arsiv_kat, overwrites=yeni_overwrites)
        
        for child in self.children:
            child.disabled = True
        await interaction.message.edit(view=self)
        
        kid = interaction.channel.name.replace("kargo-", "").upper()
        if kid in orders: orders[kid]["durum"] = "Teslim Edildi / Sonlandırıldı"
        
        await interaction.response.send_message("🔒 **Bu bilet/kargo kanalı kapatılmış ve arşive kaldırılmıştır.**")

class PersonelEkleModal(discord.ui.Modal, title="Yeni Personel Kaydı"):
    isim = discord.ui.TextInput(label="İsim Soyisim", placeholder="Örn: Hakan...", max_length=50)
    telefon = discord.ui.TextInput(label="Telefon Numarası", placeholder="Örn: 555-0192", max_length=20)
    adres = discord.ui.TextInput(label="İkametgah Adresi", style=discord.TextStyle.paragraph, placeholder="Örn: Vespucci Canals...", max_length=200)
    rol = discord.ui.TextInput(label="Şirket Rolü / Görevi", placeholder="Örn: Motorlu Kurye", max_length=50)
    foto = discord.ui.TextInput(label="Fotoğraf Linki (URL)", placeholder="Örn: https://i.imgur.com/... (İsteğe bağlı)", required=False)

    async def on_submit(self, interaction: discord.Interaction):
        guild = interaction.guild
        kategori_isim = "📁 PERSONEL DOSYALARI"
        kategori = discord.utils.get(guild.categories, name=kategori_isim)
        
        # Kategoriyi sadece Şube Müdürleri (ve adminler) görebilecek şekilde ayarlıyoruz
        if not kategori:
            overwrites = { guild.default_role: discord.PermissionOverwrite(read_messages=False) }
            yonetici_rol = discord.utils.get(guild.roles, name="Şube Müdürü")
            if yonetici_rol:
                overwrites[yonetici_rol] = discord.PermissionOverwrite(read_messages=True, send_messages=True)
            kategori = await guild.create_category(kategori_isim, overwrites=overwrites)
            
        kanal_adi = self.isim.value.lower().replace(" ", "-")
        
        # Çift kanal oluşumunu engellemek için mevcut kanal kontrolü
        mevcut_kanal = discord.utils.get(kategori.text_channels, name=kanal_adi)
        if mevcut_kanal:
            await interaction.response.send_message(f"❌ **{self.isim.value}** adlı personelin dosyası zaten mevcut: {mevcut_kanal.mention}", ephemeral=True)
            return
            
        dosya_kanali = await guild.create_text_channel(kanal_adi, category=kategori)
        
        embed = discord.Embed(title=f"📋 Personel Dosyası: {self.isim.value}", color=discord.Color.blue())
        embed.set_thumbnail(url="https://i.imgur.com/OXvf62G.png")
        embed.add_field(name="İsim Soyisim", value=self.isim.value, inline=True)
        embed.add_field(name="Telefon", value=self.telefon.value, inline=True)
        embed.add_field(name="Şirket Rolü", value=self.rol.value, inline=True)
        embed.add_field(name="Adres", value=self.adres.value, inline=False)
        
        if self.foto.value and self.foto.value.startswith("http"):
            embed.set_image(url=self.foto.value)
            
        await dosya_kanali.send(embed=embed, view=PersonelArsivView())
        await interaction.response.send_message(f"✅ Personel dosyası başarıyla oluşturuldu: {dosya_kanali.mention}", ephemeral=True)

class PersonelDuzenleModal(discord.ui.Modal, title="Personel Bilgilerini Düzenle"):
    isim = discord.ui.TextInput(label="İsim Soyisim", max_length=50)
    telefon = discord.ui.TextInput(label="Telefon Numarası", max_length=20)
    adres = discord.ui.TextInput(label="İkametgah Adresi", style=discord.TextStyle.paragraph, max_length=200)
    rol = discord.ui.TextInput(label="Şirket Rolü / Görevi", max_length=50)
    foto = discord.ui.TextInput(label="Fotoğraf Linki (URL)", required=False)

    def __init__(self, embed: discord.Embed):
        super().__init__()
        # Mevcut veriyi formun içine varsayılan değer olarak çekiyoruz
        if len(embed.fields) >= 4:
            self.isim.default = embed.fields[0].value
            self.telefon.default = embed.fields[1].value
            self.rol.default = embed.fields[2].value
            self.adres.default = embed.fields[3].value
        if embed.image and embed.image.url:
            self.foto.default = embed.image.url

    async def on_submit(self, interaction: discord.Interaction):
        embed = interaction.message.embeds[0]
        embed.title = f"📋 Personel Dosyası: {self.isim.value}"
        embed.set_field_at(0, name="İsim Soyisim", value=self.isim.value, inline=True)
        embed.set_field_at(1, name="Telefon", value=self.telefon.value, inline=True)
        embed.set_field_at(2, name="Şirket Rolü", value=self.rol.value, inline=True)
        embed.set_field_at(3, name="Adres", value=self.adres.value, inline=False)
        
        if self.foto.value and self.foto.value.startswith("http"):
            embed.set_image(url=self.foto.value)
        else:
            embed.set_image(url=None)
            
        await interaction.message.edit(embed=embed)
        
        # İsim değiştiyse kanalın adını da otomatik günceller
        try:
            kanal_adi = self.isim.value.lower().replace(" ", "-")
            await interaction.channel.edit(name=kanal_adi)
        except:
            pass
            
        await interaction.response.send_message("✅ Personel bilgileri başarıyla güncellendi.", ephemeral=True)

class PersonelArsivView(discord.ui.View):
    def __init__(self):
        super().__init__(timeout=None)
        
    @discord.ui.button(label="✏️ Bilgileri Düzenle", style=discord.ButtonStyle.primary, custom_id="personel_duzenle_btn")
    async def btn_duzenle(self, interaction: discord.Interaction, button: discord.ui.Button):
        if not (interaction.user.guild_permissions.administrator or interaction.user.name == "saintvor_" or any(r.name == "Şube Müdürü" for r in interaction.user.roles)):
            await interaction.response.send_message("❌ Bu işlem için şirket yetkiniz yok.", ephemeral=True)
            return
        embed = interaction.message.embeds[0]
        await interaction.response.send_modal(PersonelDuzenleModal(embed))

    @discord.ui.button(label="📦 Dosyayı Arşive Kaldır", style=discord.ButtonStyle.danger, custom_id="personel_arsivle_btn")
    async def btn_arsivle(self, interaction: discord.Interaction, button: discord.ui.Button):
        # Yalnızca yetkililer bu dosyayı arşivleyebilir
        if not (interaction.user.guild_permissions.administrator or interaction.user.name == "saintvor_" or any(r.name == "Şube Müdürü" for r in interaction.user.roles)):
            await interaction.response.send_message("❌ Bu işlem için şirket yetkiniz yok.", ephemeral=True)
            return
            
        guild = interaction.guild
        arsiv_kategori = discord.utils.get(guild.categories, name="📁 İŞTEN AYRILANLAR (ARŞİV)")
        if not arsiv_kategori:
            overwrites = { guild.default_role: discord.PermissionOverwrite(read_messages=False) }
            yonetici_rol = discord.utils.get(guild.roles, name="Şube Müdürü")
            if yonetici_rol:
                overwrites[yonetici_rol] = discord.PermissionOverwrite(read_messages=True, send_messages=False)
            arsiv_kategori = await guild.create_category("📁 İŞTEN AYRILANLAR (ARŞİV)", overwrites=overwrites)
            
        await interaction.channel.edit(category=arsiv_kategori)
        await interaction.response.send_message("📦 **Bu personel dosyası arşivlenmiş ve kanalı taşınmıştır.**")
        
        # Arşive kaldırıldıktan sonra butonu devre dışı bırak
        button.disabled = True
        await interaction.message.edit(view=self)

class PersonelPanelView(discord.ui.View):
    def __init__(self):
        super().__init__(timeout=None)
    @discord.ui.button(label="👥 Yeni Personel Ekle", style=discord.ButtonStyle.success, custom_id="personel_ekle_btn")
    async def btn_ekle(self, interaction: discord.Interaction, button: discord.ui.Button):
        await interaction.response.send_modal(PersonelEkleModal())

# --- TESLİMAT MAKBUZU VE FATURA SİSTEMİ ---

class FaturaModal(discord.ui.Modal):
    def __init__(self, hedef: discord.Member):
        super().__init__(title=f"Fatura Kes: {hedef.display_name}"[:45])
        self.hedef_uye = hedef

    tarih = discord.ui.TextInput(label="Tarih", placeholder="Örn: 25.05.2026", max_length=20)
    hizmet = discord.ui.TextInput(label="Hizmet / Kargo İçeriği", placeholder="Örn: Vespucci Ağır Yük Teslimatı", max_length=100)
    fiyat = discord.ui.TextInput(label="Tutar / Fiyat", placeholder="Örn: $500", max_length=20)
    foto = discord.ui.TextInput(label="Makbuz / Fotoğraf Linki (İsteğe Bağlı)", placeholder="Örn: https://i.imgur.com/...", required=False)

    async def on_submit(self, interaction: discord.Interaction):
        embed = discord.Embed(title="🧾 Teslimat Makbuzu / Fatura", color=discord.Color.green())
        embed.set_thumbnail(url="https://i.imgur.com/OXvf62G.png")
        embed.add_field(name="Müşteri", value=self.hedef_uye.mention, inline=True)
        embed.add_field(name="Tarih", value=self.tarih.value, inline=True)
        embed.add_field(name="Tutar", value=self.fiyat.value, inline=False)
        embed.add_field(name="Açıklama", value=self.hizmet.value, inline=False)
        
        if self.foto.value and self.foto.value.startswith("http"):
            embed.set_image(url=self.foto.value)
            
        embed.set_footer(text="Bizi Tercih Ettiğiniz İçin Teşekkürler! | GoPostal Teslimat")
        
        await interaction.channel.send(f"🔔 {self.hedef_uye.mention}, yeni bir teslimat faturanız oluşturuldu!", embed=embed)
        
        try:
            await self.hedef_uye.send("🧾 **GoPostal:** Yeni bir teslimat faturanız var!", embed=embed)
        except discord.Forbidden:
            pass
            
        await yoneticilere_bildir(interaction.guild, embed)
        await interaction.response.send_message("✅ Fatura başarıyla kesildi. Müşteriye ve Yöneticilere iletildi.", ephemeral=True)

class FaturaHedefSecView(discord.ui.View):
    def __init__(self):
        super().__init__(timeout=180)
    @discord.ui.select(cls=discord.ui.UserSelect, placeholder="Fatura kesilecek müşteriyi seçin...")
    async def select_user(self, interaction: discord.Interaction, select: discord.ui.UserSelect):
        await interaction.response.send_modal(FaturaModal(hedef=select.values[0]))

class FaturaPanelView(discord.ui.View):
    def __init__(self):
        super().__init__(timeout=None)
    @discord.ui.button(label="🧾 Fatura / Makbuz Kes", style=discord.ButtonStyle.success, custom_id="fatura_kes_btn")
    async def btn_fatura(self, interaction: discord.Interaction, button: discord.ui.Button):
        try:
            await interaction.response.send_message("Lütfen faturanın kesileceği müşteriyi seçin:", view=FaturaHedefSecView(), ephemeral=True)
        except discord.HTTPException:
            pass

# --- YENİ MODÜLLER (ŞİKAYET, ÖNERİ, ABONELİK, İK, İZİN, MUHASEBE) ---

class SikayetModal(discord.ui.Modal, title="Personel Şikayet Formu"):
    edilen = discord.ui.TextInput(label="Şikayet Ettiğiniz Personel", max_length=50)
    tarih = discord.ui.TextInput(label="Tarih", placeholder="Örn: 25.05.2026", max_length=20)
    sebep = discord.ui.TextInput(label="Sebebi", style=discord.TextStyle.paragraph)

    async def on_submit(self, interaction: discord.Interaction):
        kanal = discord.utils.get(interaction.guild.text_channels, name="gelen-şikayetler")
        if kanal:
            embed = discord.Embed(title="🚨 Yeni Personel Şikayeti", color=discord.Color.red())
            embed.add_field(name="Şikayet Eden", value=interaction.user.mention, inline=False)
            embed.add_field(name="Edilen Personel", value=self.edilen.value, inline=True)
            embed.add_field(name="Tarih", value=self.tarih.value, inline=True)
            embed.add_field(name="Sebep", value=self.sebep.value, inline=False)
            await kanal.send(embed=embed)
        await interaction.response.send_message("✅ Şikayetiniz gizli bir şekilde şirket yönetimine iletildi.", ephemeral=True)

class SikayetView(discord.ui.View):
    def __init__(self):
        super().__init__(timeout=None)
    @discord.ui.button(label="🚨 Şikayet Formu Doldur", style=discord.ButtonStyle.danger, custom_id="sikayet_btn")
    async def btn_sikayet(self, interaction: discord.Interaction, button: discord.ui.Button):
        await interaction.response.send_modal(SikayetModal())

class OneriModal(discord.ui.Modal, title="Şirket Öneri Formu"):
    isim = discord.ui.TextInput(label="Adınız Soyadınız", max_length=50)
    oneri = discord.ui.TextInput(label="Öneriniz / Fikriniz", style=discord.TextStyle.paragraph)

    async def on_submit(self, interaction: discord.Interaction):
        kanal = discord.utils.get(interaction.guild.text_channels, name="gelen-öneriler")
        if kanal:
            embed = discord.Embed(title="💡 Yeni Şirket Önerisi", color=discord.Color.blue())
            embed.add_field(name="Gönderen", value=f"{interaction.user.mention} ({self.isim.value})", inline=False)
            embed.add_field(name="Öneri", value=self.oneri.value, inline=False)
            await kanal.send(embed=embed)
        await interaction.response.send_message("✅ Değerli fikriniz için teşekkürler! Yönetime iletildi.", ephemeral=True)

class OneriView(discord.ui.View):
    def __init__(self):
        super().__init__(timeout=None)
    @discord.ui.button(label="💡 Fikir / Öneri Gönder", style=discord.ButtonStyle.primary, custom_id="oneri_btn")
    async def btn_oneri(self, interaction: discord.Interaction, button: discord.ui.Button):
        await interaction.response.send_modal(OneriModal())

class AbonelikDuzenleModal(discord.ui.Modal, title="Abonelik Düzenle"):
    sirket = discord.ui.TextInput(label="Şirket Adı / İsim Soyisim", max_length=50)
    tarih = discord.ui.TextInput(label="Tarih", max_length=20)
    
    def __init__(self, embed: discord.Embed):
        super().__init__()
        self.sirket.default = embed.fields[0].value
        self.tarih.default = embed.fields[1].value
        
    async def on_submit(self, interaction: discord.Interaction):
        embed = interaction.message.embeds[0]
        embed.set_field_at(0, name="Şirket / Kişi", value=self.sirket.value, inline=False)
        embed.set_field_at(1, name="Tarih", value=self.tarih.value, inline=False)
        await interaction.message.edit(embed=embed)
        await interaction.response.send_message("✅ Abonelik bilgileri güncellendi.", ephemeral=True)

class AbonelikYonetimView(discord.ui.View):
    def __init__(self):
        super().__init__(timeout=None)
        
    @discord.ui.button(label="✅ Onayla & Aktif Et", style=discord.ButtonStyle.success, custom_id="abone_onayla")
    async def btn_onayla(self, interaction: discord.Interaction, button: discord.ui.Button):
        if not (interaction.user.guild_permissions.administrator or any(r.name == "Şube Müdürü" for r in interaction.user.roles)):
            return await interaction.response.send_message("❌ Yetkiniz yok.", ephemeral=True)
        kat = discord.utils.get(interaction.guild.categories, name="🟢 AKTİF ABONELER") or await interaction.guild.create_category("🟢 AKTİF ABONELER")
        await interaction.channel.edit(category=kat)
        embed = interaction.message.embeds[0]
        embed.color = discord.Color.green()
        embed.set_field_at(2, name="Durum", value="Aktif", inline=False)
        embed.title = embed.title.replace("🟡 ", "🟢 ").replace("🔴 ", "🟢 ")
        await interaction.message.edit(embed=embed)
        await interaction.response.send_message("✅ Abonelik onaylandı ve aktife alındı.", ephemeral=True)

    @discord.ui.button(label="🔴 Pasife Al", style=discord.ButtonStyle.danger, custom_id="abone_pasif")
    async def btn_pasif(self, interaction: discord.Interaction, button: discord.ui.Button):
        if not (interaction.user.guild_permissions.administrator or any(r.name == "Şube Müdürü" for r in interaction.user.roles)):
            return await interaction.response.send_message("❌ Yetkiniz yok.", ephemeral=True)
        kat = discord.utils.get(interaction.guild.categories, name="🔴 PASİF ABONELER") or await interaction.guild.create_category("🔴 PASİF ABONELER")
        await interaction.channel.edit(category=kat)
        embed = interaction.message.embeds[0]
        embed.color = discord.Color.red()
        embed.set_field_at(2, name="Durum", value="Pasif", inline=False)
        embed.title = embed.title.replace("🟡 ", "🔴 ").replace("🟢 ", "🔴 ")
        await interaction.message.edit(embed=embed)
        await interaction.response.send_message("🔴 Abonelik pasife alındı.", ephemeral=True)
        
    @discord.ui.button(label="✏️ Düzenle", style=discord.ButtonStyle.primary, custom_id="abone_duzenle")
    async def btn_duzenle(self, interaction: discord.Interaction, button: discord.ui.Button):
        if not (interaction.user.guild_permissions.administrator or any(r.name == "Şube Müdürü" for r in interaction.user.roles)):
            return await interaction.response.send_message("❌ Yetkiniz yok.", ephemeral=True)
        await interaction.response.send_modal(AbonelikDuzenleModal(interaction.message.embeds[0]))

class AbonelikModal(discord.ui.Modal, title="Abonelik Başvurusu"):
    sirket = discord.ui.TextInput(label="Şirket Adı / İsim Soyisim", max_length=50)
    tarih = discord.ui.TextInput(label="Tarih", placeholder="Örn: 25.05.2026", max_length=20)

    async def on_submit(self, interaction: discord.Interaction):
        guild = interaction.guild
        ow = { guild.default_role: discord.PermissionOverwrite(read_messages=False) }
        yonetici_rol = discord.utils.get(guild.roles, name="Şube Müdürü")
        if yonetici_rol: ow[yonetici_rol] = discord.PermissionOverwrite(read_messages=True, send_messages=True)
        
        kat = discord.utils.get(guild.categories, name="📁 ABONELİK TALEPLERİ") or await guild.create_category("📁 ABONELİK TALEPLERİ", overwrites=ow)
        kanal_adi = f"abone-{self.sirket.value.lower().replace(' ', '-')}"
        kanal = await guild.create_text_channel(kanal_adi, category=kat, overwrites=ow)
        
        embed = discord.Embed(title=f"🟡 Abonelik Talebi: {self.sirket.value}", color=discord.Color.gold())
        embed.add_field(name="Şirket / Kişi", value=self.sirket.value, inline=False)
        embed.add_field(name="Tarih", value=self.tarih.value, inline=False)
        embed.add_field(name="Durum", value="Beklemede", inline=False)
        embed.set_thumbnail(url="https://i.imgur.com/OXvf62G.png")
        
        await kanal.send(embed=embed, view=AbonelikYonetimView())
        await interaction.response.send_message(f"✅ Abonelik talebiniz alındı. Yetkililer kanal üzerinden işlem yapacaktır.", ephemeral=True)

class AbonelikPanelView(discord.ui.View):
    def __init__(self):
        super().__init__(timeout=None)
    @discord.ui.button(label="📝 Abone Ol", style=discord.ButtonStyle.success, custom_id="abone_ol_btn")
    async def btn_abone(self, interaction: discord.Interaction, button: discord.ui.Button):
        await interaction.response.send_modal(AbonelikModal())

class FiyatListesiModal(discord.ui.Modal, title="Fiyat Listesini Düzenle"):
    fiyatlar = discord.ui.TextInput(label="Yeni Fiyat Listesi", style=discord.TextStyle.paragraph, max_length=2000)
    def __init__(self, embed: discord.Embed):
        super().__init__()
        self.fiyatlar.default = embed.description
    async def on_submit(self, interaction: discord.Interaction):
        embed = interaction.message.embeds[0]
        embed.description = self.fiyatlar.value
        await interaction.message.edit(embed=embed)
        await interaction.response.send_message("✅ Fiyat listesi güncellendi.", ephemeral=True)

class FiyatListesiView(discord.ui.View):
    def __init__(self):
        super().__init__(timeout=None)
    @discord.ui.button(label="✏️ Fiyatları Düzenle", style=discord.ButtonStyle.secondary, custom_id="fiyat_duzenle_btn")
    async def btn_duzenle(self, interaction: discord.Interaction, button: discord.ui.Button):
        if not (interaction.user.guild_permissions.administrator or any(r.name == "Şube Müdürü" for r in interaction.user.roles)):
            return await interaction.response.send_message("❌ Yetkiniz yok.", ephemeral=True)
        await interaction.response.send_modal(FiyatListesiModal(interaction.message.embeds[0]))

class IsBasvuruModal(discord.ui.Modal, title="İş Başvurusu Formu"):
    isim = discord.ui.TextInput(label="İsim Soyisim", max_length=50)
    numara = discord.ui.TextInput(label="Telefon Numarası", max_length=20)
    adres = discord.ui.TextInput(label="Adres", style=discord.TextStyle.short)
    neden = discord.ui.TextInput(label="Neden Katılmak İstiyorsunuz?", style=discord.TextStyle.paragraph)
    alan = discord.ui.TextInput(label="Hangi Alan (Kurye, Lojistik, vb.)", max_length=50)

    async def on_submit(self, interaction: discord.Interaction):
        guild = interaction.guild
        ow = { guild.default_role: discord.PermissionOverwrite(read_messages=False) }
        yonetici_rol = discord.utils.get(guild.roles, name="Şube Müdürü")
        if yonetici_rol: ow[yonetici_rol] = discord.PermissionOverwrite(read_messages=True, send_messages=True)
        
        kat = discord.utils.get(guild.categories, name="📁 İŞ BAŞVURULARI") or await guild.create_category("📁 İŞ BAŞVURULARI", overwrites=ow)
        kanal_adi = f"başvuru-{self.isim.value.lower().replace(' ', '-')}"
        kanal = await guild.create_text_channel(kanal_adi, category=kat, overwrites=ow)
        
        embed = discord.Embed(title=f"📋 Yeni İş Başvurusu: {self.isim.value}", color=discord.Color.blue())
        embed.add_field(name="Başvuran", value=interaction.user.mention, inline=False)
        embed.add_field(name="Telefon", value=self.numara.value, inline=True)
        embed.add_field(name="Alan", value=self.alan.value, inline=True)
        embed.add_field(name="Adres", value=self.adres.value, inline=False)
        embed.add_field(name="Neden Katılmak İstiyor?", value=self.neden.value, inline=False)
        await kanal.send(embed=embed)
        await interaction.response.send_message("✅ Başvurunuz İnsan Kaynakları departmanına iletildi.", ephemeral=True)

class IsBasvuruView(discord.ui.View):
    def __init__(self):
        super().__init__(timeout=None)
    @discord.ui.button(label="📝 İş Başvurusu Yap", style=discord.ButtonStyle.success, custom_id="is_basvurusu_btn")
    async def btn_basvuru(self, interaction: discord.Interaction, button: discord.ui.Button):
        await interaction.response.send_modal(IsBasvuruModal())

class IzinArsivView(discord.ui.View):
    def __init__(self):
        super().__init__(timeout=None)
    @discord.ui.button(label="📦 Arşive At", style=discord.ButtonStyle.danger, custom_id="izin_arsivle_btn")
    async def btn_arsivle(self, interaction: discord.Interaction, button: discord.ui.Button):
        if not (interaction.user.guild_permissions.administrator or any(r.name == "Şube Müdürü" for r in interaction.user.roles)):
            return await interaction.response.send_message("❌ Yetkiniz yok.", ephemeral=True)
        kat = discord.utils.get(interaction.guild.categories, name="📁 ARŞİVLENEN İZİNLER") or await interaction.guild.create_category("📁 ARŞİVLENEN İZİNLER")
        await interaction.channel.edit(category=kat)
        await interaction.response.send_message("📦 İzin dosyası arşive kaldırıldı.")
        button.disabled = True
        await interaction.message.edit(view=self)

class IzinTalebiModal(discord.ui.Modal, title="İzin Talebi Formu"):
    personel = discord.ui.TextInput(label="İzin Talep Eden Personel", max_length=50)
    gun = discord.ui.TextInput(label="Gün Sayısı", max_length=10)
    sebep = discord.ui.TextInput(label="Sebep", style=discord.TextStyle.paragraph)

    async def on_submit(self, interaction: discord.Interaction):
        guild = interaction.guild
        ow = { guild.default_role: discord.PermissionOverwrite(read_messages=False), interaction.user: discord.PermissionOverwrite(read_messages=True, send_messages=True) }
        yonetici_rol = discord.utils.get(guild.roles, name="Şube Müdürü")
        if yonetici_rol: ow[yonetici_rol] = discord.PermissionOverwrite(read_messages=True, send_messages=True)
        
        kat = discord.utils.get(guild.categories, name="🏖️ İZİN TALEPLERİ") or await guild.create_category("🏖️ İZİN TALEPLERİ")
        kanal_adi = f"izin-{self.personel.value.lower().replace(' ', '-')}"
        kanal = await guild.create_text_channel(kanal_adi, category=kat, overwrites=ow)
        
        embed = discord.Embed(title=f"🏖️ İzin Talebi: {self.personel.value}", color=discord.Color.orange())
        embed.add_field(name="Talep Eden", value=interaction.user.mention, inline=False)
        embed.add_field(name="Gün Sayısı", value=self.gun.value, inline=True)
        embed.add_field(name="Sebep", value=self.sebep.value, inline=False)
        await kanal.send(embed=embed, view=IzinArsivView())
        await interaction.response.send_message(f"✅ İzin talebiniz yetkililere iletildi: {kanal.mention}", ephemeral=True)

class IzinTalebiView(discord.ui.View):
    def __init__(self):
        super().__init__(timeout=None)
    @discord.ui.button(label="🏖️ İzin Talebi Oluştur", style=discord.ButtonStyle.primary, custom_id="izin_talep_btn")
    async def btn_izin(self, interaction: discord.Interaction, button: discord.ui.Button):
        await interaction.response.send_modal(IzinTalebiModal())

class ZamTalebiModal(discord.ui.Modal, title="İK - Zam Talebi"):
    isim = discord.ui.TextInput(label="İsim Soyisim", max_length=50)
    sebep = discord.ui.TextInput(label="Zam Sebebi", style=discord.TextStyle.paragraph)

    async def on_submit(self, interaction: discord.Interaction):
        guild = interaction.guild
        ow = { guild.default_role: discord.PermissionOverwrite(read_messages=False), interaction.user: discord.PermissionOverwrite(read_messages=True, send_messages=True) }
        yonetici_rol = discord.utils.get(guild.roles, name="Şube Müdürü")
        if yonetici_rol: ow[yonetici_rol] = discord.PermissionOverwrite(read_messages=True, send_messages=True)
        
        kat = discord.utils.get(guild.categories, name="💰 MUHASEBE VE MAAŞ") or await guild.create_category("💰 MUHASEBE VE MAAŞ")
        kanal = await guild.create_text_channel(f"zam-{interaction.user.name}", category=kat, overwrites=ow)
        
        embed = discord.Embed(title=f"📈 Zam Talebi: {self.isim.value}", color=discord.Color.gold())
        embed.add_field(name="Talep Eden", value=interaction.user.mention, inline=False)
        embed.add_field(name="Sebep", value=self.sebep.value, inline=False)
        await kanal.send(embed=embed)
        await interaction.response.send_message(f"✅ Zam talebiniz muhasebeye iletildi: {kanal.mention}", ephemeral=True)

class ZamTalebiView(discord.ui.View):
    def __init__(self):
        super().__init__(timeout=None)
    @discord.ui.button(label="📈 Maaş Zam Talebi Aç", style=discord.ButtonStyle.success, custom_id="zam_talep_btn")
    async def btn_zam(self, interaction: discord.Interaction, button: discord.ui.Button):
        await interaction.response.send_modal(ZamTalebiModal())

@bot.event
async def on_ready():
    bot.add_view(TicketView()) # Bot her yeniden başladığında butonu aktif tutar
    bot.add_view(MailView())
    bot.add_view(SiparisGorevliView())
    bot.add_view(SiparisMusteriView())
    bot.add_view(DestekView())
    bot.add_view(MesaiView())
    bot.add_view(AracRaporView())
    bot.add_view(PersonelPanelView())
    bot.add_view(PersonelArsivView())
    bot.add_view(SikayetView())
    bot.add_view(OneriView())
    bot.add_view(AbonelikPanelView())
    bot.add_view(AbonelikYonetimView())
    bot.add_view(FiyatListesiView())
    bot.add_view(IsBasvuruView())
    bot.add_view(IzinTalebiView())
    bot.add_view(IzinArsivView())
    bot.add_view(FiyatListesiView()) # Maaş Listesi için aynı Edit sınıfı kullanılabilir
    bot.add_view(ZamTalebiView())
    bot.add_view(FaturaPanelView())
    bot.add_view(KargoKanalView())
    print(f'✅ {bot.user} başarıyla çalıştı! GoPostal sunucusu kuruluma hazır.')

@bot.event
async def on_message(message):
    # Ekranların sürekli temiz kalması için otomatik silme sistemi
    panel_kanallari = ["mail-sistemi", "aktif-kargolar", "kargo-talebi-oluştur", "kargo-nerede", "müşteri-destek", "mesai-takip", "araç-durum-raporları", "personel-şikayet", "öneriler", "abonelik-başvurusu", "fiyat-listesi", "iş-başvurusu", "izin-talebi-oluştur", "maaş-listesi", "ik-zam-talebi"]
    
    # Eğer mesaj panel kanalındaysa ve BOT DIŞINDA BİRİ yazdıysa anında siler.
    # Bu ayar botun yanlışlıkla kendi oluşturduğu panelleri silmesini kesin olarak engeller.
    if message.channel.name in panel_kanallari and message.author != bot.user:
        try:
            await message.delete(delay=2)
        except:
            pass
                
    await bot.process_commands(message)

@bot.command()
@is_admin_or_saintvor()
async def gopostalkur(ctx):
    guild = ctx.guild
    msg = await ctx.send("📦 GoPostal sistemleri başlatılıyor. Mevcut tüm veriler (klonlar dahil acımasızca) temizleniyor, lütfen bekleyin...")

    # 1. ESKİ KANALLARI, KATEGORİLERİ VE KLONLARI TEMİZLEME
    hedef_kategoriler = [
        "🏢 Merkez Ofis", "🚚 Lojistik ve Teslimat", "📞 Müşteri Hizmetleri", 
        "📻 Telsiz ve Dinlenme", "📩 GELEN MAILLER", "📦 KARGO TALEPLERİ", 
        "📻 Telsiz ve Dinlenme", "📩 GELEN MAILLER", "� KARGO TALEPLERİ", "📦 GENEL KARGO İSTEKLERİ",
        "�📁 ARŞİVLENEN KARGOLAR", "📝 ŞİKAYET VE ÖNERİLER", "🤝 ABONELİK SİSTEMİ", 
        "🟢 AKTİF ABONELER", "🔴 PASİF ABONELER", "💼 İNSAN KAYNAKLARI", 
        "🏖️ İZİN TALEPLERİ", "📁 ARŞİVLENEN İZİNLER", "💰 MUHASEBE VE MAAŞ", 
        "📁 İŞ BAŞVURULARI", "📁 ABONELİK TALEPLERİ", "📁 PERSONEL DOSYALARI", 
        "📁 İŞTEN AYRILANLAR (ARŞİV)"
    ]
    
    for kategori in guild.categories:
        if kategori.name in hedef_kategoriler:
            for kanal in kategori.channels:
                try:
                    await kanal.delete()
                except:
                    pass
            try:
                await kategori.delete()
            except:
                pass

    hedef_kanallar = [
        "şirket-kuralları", "duyurular", "mesai-takip", "mail-sistemi", 
        "aktif-kargolar", "teslimat-kanıtları", "araç-durum-raporları", 
        "kargo-talebi-oluştur", "kargo-nerede", "müşteri-destek", "telsiz-sohbet", 
        "medya-ve-fotoğraflar", "personel-şikayet", "öneriler", "gelen-şikayetler", 
        "gelen-öneriler", "abonelik-başvurusu", "fiyat-listesi", "iş-başvurusu", 
        "izin-talebi-oluştur", "maaş-listesi", "ik-zam-talebi"
    ]
    
    for kanal in guild.channels:
        if kanal.name in hedef_kanallar:
            try:
                await kanal.delete()
            except:
                pass
        elif isinstance(kanal, discord.TextChannel) and (kanal.name.startswith("kargo-") or kanal.name.startswith("destek-") or kanal.name.startswith("bilet-") or kanal.name.startswith("izin-") or kanal.name.startswith("zam-") or kanal.name.startswith("abone-") or kanal.name.startswith("başvuru-")):
            try:
                await kanal.delete()
            except:
                pass
                
    # 2. ESKİ ROLLERİ TEMİZLEME
    roller_isimler = ["Şube Müdürü", "Lojistik Uzmanı", "Kargo Aracı Şoförü", "Motorlu Kurye", "Müşteri", "LEGAL FM"]
    for rol in guild.roles:
        if rol.name in roller_isimler:
            try:
                await rol.delete()
            except discord.Forbidden:
                pass
            except discord.HTTPException:
                pass

    await msg.edit(content="📦 Eski sistemler temizlendi. Renkli roller ve kısıtlı yetkiler ayarlanıyor (Klonlama engellendi)...")

    # 3. YENİ RENKLİ VE YETKİLİ ROLLER (Anti-Crash Korumalı)
    async def guvenli_rol(isim, renk):
        rol = discord.utils.get(guild.roles, name=isim)
        if not rol:
            try: rol = await guild.create_role(name=isim, color=renk, hoist=True)
            except: pass
        return rol

    rol_yonetici = await guvenli_rol("Şube Müdürü", discord.Color.red())
    rol_lojistik = await guvenli_rol("Lojistik Uzmanı", discord.Color.orange())
    rol_sofor = await guvenli_rol("Kargo Aracı Şoförü", discord.Color.gold())
    rol_kurye = await guvenli_rol("Motorlu Kurye", discord.Color.green())
    rol_musteri = await guvenli_rol("Müşteri", discord.Color.blue())
    rol_legal = await guvenli_rol("LEGAL FM", discord.Color.purple())

    # 4. YETKİ (OVERWRITE) AYARLARI
    sirket_gizli = { guild.default_role: discord.PermissionOverwrite(read_messages=False) }
    if rol_musteri: sirket_gizli[rol_musteri] = discord.PermissionOverwrite(read_messages=False)
    for r in [rol_yonetici, rol_lojistik, rol_sofor, rol_kurye, rol_legal]:
        if r: sirket_gizli[r] = discord.PermissionOverwrite(read_messages=True, send_messages=True)

    halka_acik = { guild.default_role: discord.PermissionOverwrite(read_messages=False) }
    if rol_musteri: halka_acik[rol_musteri] = discord.PermissionOverwrite(read_messages=True, send_messages=True)
    for r in [rol_yonetici, rol_lojistik, rol_sofor, rol_kurye, rol_legal]:
        if r: halka_acik[r] = discord.PermissionOverwrite(read_messages=True, send_messages=True)

    sirket_yonetim_gizli = { guild.default_role: discord.PermissionOverwrite(read_messages=False) }
    if rol_yonetici: sirket_yonetim_gizli[rol_yonetici] = discord.PermissionOverwrite(read_messages=True, send_messages=True)

    await msg.edit(content="📦 Kategoriler, kanallar ve butonlar 1'er adet olacak şekilde oluşturuluyor...")

    # Klonlamayı ve Çökmeyi Önleyen Akıllı Kurucu Fonksiyonlar
    async def setup_category(name, overwrites=None):
        kat = discord.utils.get(guild.categories, name=name)
        try:
            if not kat:
                if overwrites is not None:
                    kat = await guild.create_category(name, overwrites=overwrites)
                else:
                    kat = await guild.create_category(name)
                await asyncio.sleep(0.5) # Discord API limitlerine (Rate Limit) takılmamak için bekleme süresi
            elif overwrites is not None: 
                await kat.edit(overwrites=overwrites)
        except Exception as e: 
            print(f"Kategori hatası ({name}): {e}")
        return kat

    async def setup_channel(kat, name, embed=None, view=None, overwrites=None):
        if not kat: return None
        ch = discord.utils.get(kat.text_channels, name=name)
        try:
            if not ch:
                if overwrites is not None:
                    ch = await guild.create_text_channel(name, category=kat, overwrites=overwrites)
                else:
                    ch = await guild.create_text_channel(name, category=kat)
                await asyncio.sleep(0.5)
            else:
                if overwrites is not None: 
                    await ch.edit(overwrites=overwrites)
                try: await ch.purge(limit=50)
                except: pass
            if embed: await ch.send(embed=embed, view=view)
            return ch
        except Exception as e: 
            print(f"Kanal hatası ({name}): {e}")
            return None

    # 5. KATEGORİ VE KANALLARI SIFIRDAN KURMA (İÇLERİNDE BUTONLARLA BİRLİKTE)
    
    # Merkez Ofis
    kat_merkez = await setup_category("🏢 Merkez Ofis", sirket_gizli)
    
    kurallar_metni = """**1. Genel Davranış ve Profesyonellik**
**Kıyafet Zorunluluğu:** Görev başında her zaman şirket üniforması eksiksiz giyilmelidir. Dağınık veya kirli kıyafetle çalışmak kesinlikle yasaktır.
**Müşteri İlişkileri:** Müşterilerimize karşı her zaman nazik, saygılı ve sabırlı olunmalıdır. Herhangi bir tartışma durumunda soğukkanlılığınızı koruyun; sorun büyümesi halinde derhal süpervizörünüze haber verin.
**Gizlilik:** Müşterilerin gönderileri hakkında bilgi almak, paketleri incelemek veya içerikleriyle ilgili üçüncü şahıslarla konuşmak kesinlikle yasaktır.

**2. Araç ve Ekipman Kullanımı**
**Bakım Sorumluluğu:** Size tahsis edilen GoPostal aracının temizliği ve temel kontrolleri (yakıt, yağ, lastik basıncı) şoförün sorumluluğundadır.
**Trafik Kuralları:** Şirketimiz, hiçbir teslimatın insan hayatından veya trafik güvenliğinden daha değerli olduğunu savunmaz. Hız sınırlarına ve trafik işaretlerine riayet edilmelidir.
**Park Kuralları:** Teslimat sırasında araçlar yaya trafiğini veya diğer sürücüleri engelleyecek şekilde park edilmemelidir.

**3. Teslimat Protokolleri**
**Zamanlama:** "Hızımız, sözümüzdür." Teslimatların belirlenen zaman aralıklarında yapılması esastır. Gecikme yaşanacak durumlarda merkez ofis önceden bilgilendirilmelidir.
**Güvenlik:** Teslimat sırasında paketlerin hasar görmemesi en temel görevdir. Kırılacak eşya statüsündeki paketlere ekstra özen gösterilmelidir.
**İmza ve Onay:** Teslimat gerçekleştiğinde alıcının imzası veya sistem üzerinden dijital onayı mutlaka alınmalıdır. Onay alınmadan bırakılan paketlerde oluşacak kayıplardan çalışan sorumludur.

**4. Yasaklı Eylemler ve Disiplin**
**Kişisel Kullanım:** Şirket araçları ve ekipmanları mesai saatleri dışında veya kişisel işler için kullanılamaz.
**Alkol ve Madde:** Mesai saatleri içerisinde veya mesaiye alkollü/madde etkisi altında başlamak kesinlikle yasaktır ve bu durumun cezası doğrudan iş feshidir.
**İzinsiz Duraklama:** Teslimat rotası dışına çıkmak veya yetkisiz noktalarda uzun süreli duraklamalar yapmak disiplin suçu teşkil eder.

*Not: Şirket kurallarımızın ihlali durumunda, olayın ciddiyetine göre uyarı, uzaklaştırma veya iş akdinin feshi gibi yaptırımlar uygulanacaktır. GoPostal'ın prestijini korumak hepimizin görevidir.*

***

**OOC KURALLAR**
1. Forum kuralları, tüm GTAW Türkiye Discord sunucuları içerisinde geçerlidir.
2. Anlaşamadığınız durumlarda başkalarına saldırmayın, tartışmanın içine çekmeyin veya onları topluluğun önünde aşağılamayın. Herkese karşı saygılı olun.
3. NSFW, dini, siyasi veya şiddet içeren resimler, içerikler ve emojiler yasaktır.
4. Yetkililere mention (@) atmak ve spam yapmak yasaktır.
5. Özel Discord sunucuları dahil olmak üzere reklam yapmak yasaktır. Burayı Discord sunucunuzu tanıtmak veya üye toplamak için kullanamazsınız.
6. Başka kişilerin profillerine, kimliklerine bürünmeyin.
7. Genel olarak din ve siyaset tartışması döndürmeyin. Bu konulardaki konuşmalarınızda kelimelerinizi seçerek ve dikkatli kullanın.
8. Discord üzerinde oyuncu şikayetlerini tartışmayın veya atıfta bulunmayın."""

    embed_kurallar = discord.Embed(title="📜 Şirket Kuralları", description=kurallar_metni, color=discord.Color.red())
    embed_kurallar.set_thumbnail(url="https://i.imgur.com/OXvf62G.png")
    await setup_channel(kat_merkez, "şirket-kuralları", embed=embed_kurallar)
    
    await setup_channel(kat_merkez, "duyurular")
    
    embed_mesai = discord.Embed(title="⏰ Mesai Takip", description="Aşağıdaki butonları kullanarak mesai giriş ve çıkışlarınızı yapabilirsiniz.", color=discord.Color.blue())
    embed_mesai.set_thumbnail(url="https://i.imgur.com/OXvf62G.png")
    embed_mesai.add_field(name="🟢 Aktif Mesaidekiler", value="Şu an mesaide kimse yok.", inline=False)
    await setup_channel(kat_merkez, "mesai-takip", embed=embed_mesai, view=MesaiView())
    
    embed_mail = discord.Embed(title="📧 GoPostal Şirket Mail Sistemi", description="Aşağıdaki butonları kullanarak şirket içi mail gönderip alabilirsiniz:", color=discord.Color.blue())
    embed_mail.set_thumbnail(url="https://i.imgur.com/OXvf62G.png")
    await setup_channel(kat_merkez, "mail-sistemi", embed=embed_mail, view=MailView())

    # Lojistik ve Teslimat
    kat_lojistik = await setup_category("🚚 Lojistik ve Teslimat", sirket_gizli)
    
    embed_aktif = discord.Embed(title="🚚 Kargo ve Sipariş Yönetimi", description="Yetkili kurye işlemlerini aşağıdaki butonlar aracılığıyla hızlıca yapabilirsiniz:", color=discord.Color.green())
    embed_aktif.set_thumbnail(url="https://i.imgur.com/OXvf62G.png")
    await setup_channel(kat_lojistik, "aktif-kargolar", embed=embed_aktif, view=SiparisGorevliView())
    
    await setup_channel(kat_lojistik, "teslimat-kanıtları")
    
    embed_rapor = discord.Embed(title="🚐 Araç Durum Raporu", description="Hasarlı veya yakıtı biten şirket araçlarını raporlamak için aşağıdaki butonu kullanın:", color=discord.Color.orange())
    embed_rapor.set_thumbnail(url="https://i.imgur.com/OXvf62G.png")
    await setup_channel(kat_lojistik, "araç-durum-raporları", embed=embed_rapor, view=AracRaporView())

    # Müşteri Hizmetleri
    kat_musteri = await setup_category("📞 Müşteri Hizmetleri", halka_acik)
    
    embed_talep = discord.Embed(title="📬 GoPostal Kargo Talebi", description="Kargo talebi oluşturmak için aşağıdaki butona tıklayın.\n\nSizden **İsim, Telefon ve Adres** bilgileri istenecektir.", color=discord.Color.blue())
    embed_talep.set_thumbnail(url="https://i.imgur.com/OXvf62G.png")
    await setup_channel(kat_musteri, "kargo-talebi-oluştur", embed=embed_talep, view=TicketView())
    
    embed_takip = discord.Embed(title="📦 Kargo Takip Sistemi", description="Kargonuzun nerede olduğunu harita üzerinden anlık olarak görmek için butona tıklayın.", color=discord.Color.orange())
    embed_takip.set_thumbnail(url="https://i.imgur.com/OXvf62G.png")
    await setup_channel(kat_musteri, "kargo-nerede", embed=embed_takip, view=SiparisMusteriView())
    
    embed_destek = discord.Embed(title="🛠️ Müşteri Destek & Şikayet", description="Şikayet, öneri veya destek talepleriniz için aşağıdaki butonu kullanarak kayıt oluşturabilirsiniz.", color=discord.Color.red())
    embed_destek.set_thumbnail(url="https://i.imgur.com/OXvf62G.png")
    await setup_channel(kat_musteri, "müşteri-destek", embed=embed_destek, view=DestekView())

    # Telsiz ve Dinlenme
    kat_dinlenme = await setup_category("📻 Telsiz ve Dinlenme", sirket_gizli)
    await setup_channel(kat_dinlenme, "telsiz-sohbet")
    await setup_channel(kat_dinlenme, "medya-ve-fotoğraflar")

    # ŞİKAYET VE ÖNERİLER
    kat_sikayet = await setup_category("📝 ŞİKAYET VE ÖNERİLER", sirket_gizli)
    embed_sikayet = discord.Embed(title="🚨 Personel Şikayet", description="Bir personeli şikayet etmek için aşağıdaki butona tıklayarak formu doldurun. Şikayetleriniz sadece yönetim tarafından görülür.", color=discord.Color.red())
    await setup_channel(kat_sikayet, "personel-şikayet", embed=embed_sikayet, view=SikayetView())
    embed_oneri = discord.Embed(title="💡 Öneri ve Fikirler", description="Şirketimiz için önerilerinizi buradan iletebilirsiniz.", color=discord.Color.blue())
    await setup_channel(kat_sikayet, "öneriler", embed=embed_oneri, view=OneriView())
    await setup_channel(kat_sikayet, "gelen-şikayetler", overwrites=sirket_yonetim_gizli)
    await setup_channel(kat_sikayet, "gelen-öneriler", overwrites=sirket_yonetim_gizli)

    # ABONELİK SİSTEMİ
    kat_abonelik = await setup_category("🤝 ABONELİK SİSTEMİ", halka_acik)
    embed_abonelik = discord.Embed(title="🤝 Abonelik Başvurusu", description="GoPostal avantajlarından yararlanmak için Abone Olun!", color=discord.Color.gold())
    await setup_channel(kat_abonelik, "abonelik-başvurusu", embed=embed_abonelik, view=AbonelikPanelView())
    embed_fiyat = discord.Embed(title="📋 GoPostal Fiyat Listesi", description="**Standart Kurye:** $250\n**Ağır Yük Nakliyesi:** $800\n**Aylık Kurumsal Abonelik:** $5000", color=discord.Color.green())
    await setup_channel(kat_abonelik, "fiyat-listesi", embed=embed_fiyat, view=FiyatListesiView())

    # İNSAN KAYNAKLARI
    kat_ik = await setup_category("💼 İNSAN KAYNAKLARI", halka_acik)
    embed_is = discord.Embed(title="💼 GoPostal İş Başvurusu", description="Aramıza katılmak için aşağıdaki başvuru formunu doldurun.", color=discord.Color.blue())
    await setup_channel(kat_ik, "iş-başvurusu", embed=embed_is, view=IsBasvuruView())

    # İZİN TALEPLERİ
    kat_izin = await setup_category("🏖️ İZİN TALEPLERİ", sirket_gizli)
    embed_izin = discord.Embed(title="🏖️ İzin Talebi Oluştur", description="İzin kullanmak isteyen personellerimiz aşağıdaki formu doldurabilir.", color=discord.Color.orange())
    await setup_channel(kat_izin, "izin-talebi-oluştur", embed=embed_izin, view=IzinTalebiView())

    # MUHASEBE VE MAAŞ
    kat_muhasebe = await setup_category("💰 MUHASEBE VE MAAŞ", sirket_gizli)
    embed_maas = discord.Embed(title="💵 Güncel Maaş Listesi", description="**Şube Müdürü:** $10000\n**Lojistik Uzmanı:** $6000\n**Şoför & Kurye:** $4500 + Teslimat Primi", color=discord.Color.green())
    await setup_channel(kat_muhasebe, "maaş-listesi", embed=embed_maas, view=FiyatListesiView()) # Fiyat düzenleme sınıfını burası için de pratikçe kullanabiliriz
    embed_zam = discord.Embed(title="📈 İK - Zam Talebi", description="Maaşınızda artış talep etmek için formu doldurabilirsiniz.", color=discord.Color.gold())
    await setup_channel(kat_muhasebe, "ik-zam-talebi", embed=embed_zam, view=ZamTalebiView())

    await msg.edit(content="🎉 **GoPostal** ağı başarıyla güncellendi! Klonlama sorunu çözüldü, tüm sistemler (1 adet olacak şekilde) aktif.")

@bot.group(invoke_without_command=True)
@is_admin_or_saintvor()
async def sil(ctx):
    await ctx.send("Sileceğiniz öğeyi belirtin: Örn: `!sil kanallar`")

@sil.command()
@is_admin_or_saintvor()
async def kanallar(ctx):
    guild = ctx.guild
    mesaj = await ctx.send("🗑️ GoPostal sistem kanalları acımasızca temizleniyor (Klonlar dahil), lütfen bekleyin...")
    
    hedef_kategoriler = [
        "🏢 Merkez Ofis", "🚚 Lojistik ve Teslimat", "📞 Müşteri Hizmetleri", 
        "📻 Telsiz ve Dinlenme", "📩 GELEN MAILLER", "📦 KARGO TALEPLERİ", 
        "📻 Telsiz ve Dinlenme", "📩 GELEN MAILLER", "📦 KARGO TALEPLERİ", "📦 GENEL KARGO İSTEKLERİ",
        "📁 ARŞİVLENEN KARGOLAR", "📝 ŞİKAYET VE ÖNERİLER", "🤝 ABONELİK SİSTEMİ", 
        "🟢 AKTİF ABONELER", "🔴 PASİF ABONELER", "💼 İNSAN KAYNAKLARI", 
        "🏖️ İZİN TALEPLERİ", "📁 ARŞİVLENEN İZİNLER", "💰 MUHASEBE VE MAAŞ", 
        "📁 İŞ BAŞVURULARI", "📁 ABONELİK TALEPLERİ", "📁 PERSONEL DOSYALARI", 
        "📁 İŞTEN AYRILANLAR (ARŞİV)"
    ]
    
    for kategori in guild.categories:
        if kategori.name in hedef_kategoriler:
            for kanal in kategori.channels:
                try:
                    await kanal.delete()
                except:
                    pass
            try:
                await kategori.delete()
            except:
                pass

    hedef_kanallar = [
        "şirket-kuralları", "duyurular", "mesai-takip", "mail-sistemi", 
        "aktif-kargolar", "teslimat-kanıtları", "araç-durum-raporları", 
        "kargo-talebi-oluştur", "kargo-nerede", "müşteri-destek", "telsiz-sohbet", 
        "medya-ve-fotoğraflar", "personel-şikayet", "öneriler", "gelen-şikayetler", 
        "gelen-öneriler", "abonelik-başvurusu", "fiyat-listesi", "iş-başvurusu", 
        "izin-talebi-oluştur", "maaş-listesi", "ik-zam-talebi"
    ]
    
    for kanal in guild.channels:
        if kanal.name in hedef_kanallar:
            try:
                await kanal.delete()
            except:
                pass
        elif isinstance(kanal, discord.TextChannel) and (kanal.name.startswith("kargo-") or kanal.name.startswith("destek-") or kanal.name.startswith("bilet-") or kanal.name.startswith("izin-") or kanal.name.startswith("zam-") or kanal.name.startswith("abone-") or kanal.name.startswith("başvuru-")):
            try:
                await kanal.delete()
            except:
                pass

    try:
        # Silinen kanalların arasında bu mesajın atıldığı kanal da olabileceği için hata vermesini engelliyoruz
        await mesaj.edit(content="✅ Tüm GoPostal kanal ve kategorileri (ve olası klonları) başarıyla silindi.")
    except:
        pass

@bot.command()
@is_admin_or_saintvor()
async def panelyenile(ctx):
    try:
        await ctx.message.delete()
    except:
        pass
        
    async def update_panel(kanal_adi, embed, view):
        kanal = discord.utils.get(ctx.guild.text_channels, name=kanal_adi)
        if kanal:
            await kanal.purge(limit=10)
            await kanal.send(embed=embed, view=view())
            
    embed_talep = discord.Embed(title="📬 GoPostal Kargo Talebi", description="Kargo talebi oluşturmak için aşağıdaki butona tıklayın.\n\nSizden **İsim, Telefon ve Adres (Koordinat)** bilgileri istenecektir.", color=discord.Color.blue())
    embed_talep.set_thumbnail(url="https://i.imgur.com/OXvf62G.png")
    await update_panel("kargo-talebi-oluştur", embed_talep, TicketView)

    embed_aktif = discord.Embed(title="🚚 Kargo ve Sipariş Yönetimi", description="Yetkili kurye işlemlerini aşağıdaki butonlar aracılığıyla hızlıca yapabilirsiniz:", color=discord.Color.green())
    embed_aktif.set_thumbnail(url="https://i.imgur.com/OXvf62G.png")
    await update_panel("aktif-kargolar", embed_aktif, SiparisGorevliView)

    await ctx.send("🗺️ **Harita Butonları** başarıyla panellere eklendi!", delete_after=5)

@bot.command()
@is_admin_or_saintvor()
async def ticketkur(ctx):
    embed = discord.Embed(title="📬 GoPostal Bilet ve Mail Sistemi", description="Bizimle iletişime geçmek, mail atmak veya kargo talebi oluşturmak için aşağıdaki butona tıklayın.\n\nSizden **İsim, Telefon ve Adres** bilgileri istenecektir.", color=discord.Color.blue())
    embed.set_thumbnail(url="https://i.imgur.com/OXvf62G.png")
    await ctx.send(embed=embed, view=TicketView())

@bot.command()
@is_admin_or_saintvor()
async def personelpanel(ctx):
    embed = discord.Embed(title="📁 İnsan Kaynakları & Personel Yönetimi", description="Yeni bir personel kaydı açmak ve personel dosyası (kanalı) oluşturmak için aşağıdaki butonu kullanın.\n\nSistem girdiğiniz bilgilere özel bir dosya kanalı oluşturacaktır.", color=discord.Color.blue())
    embed.set_thumbnail(url="https://i.imgur.com/OXvf62G.png")
    await ctx.send(embed=embed, view=PersonelPanelView())

@bot.command()
@is_admin_or_saintvor()
async def faturapanel(ctx):
    embed = discord.Embed(title="🧾 Teslimat Makbuzu Sistemi", description="Müşterilerinize kargo faturası ve teslimat makbuzu kesmek için aşağıdaki butonu kullanın.\n\nSistem, sunucudaki müşterileri seçmenize olanak tanır.", color=discord.Color.green())
    embed.set_thumbnail(url="https://i.imgur.com/OXvf62G.png")
    await ctx.send(embed=embed, view=FaturaPanelView())

@bot.group(invoke_without_command=True)
async def mail(ctx):
    embed = discord.Embed(title="📧 GoPostal Şirket Mail Sistemi", color=discord.Color.blue())
    embed.add_field(name="Komutlar", value="`!mail gonder @Kullanıcı <Mesaj>` - Belirtilen çalışana mail atar.\n`!mail toplu <Mesaj>` - Legal FM ve Müşteri hariç tüm çalışanlara mail atar.\n`!mail oku` - Gelen maillerinizi gösterir.\n`!mail temizle` - Gelen kutunuzu boşaltır.", inline=False)
    embed.set_thumbnail(url="https://i.imgur.com/OXvf62G.png")
    embed.set_footer(text="Bu mesaj 15 saniye sonra silinecektir. Butonları kullanmanız tavsiye edilir.")
    try:
        await ctx.message.delete()
    except (discord.Forbidden, discord.NotFound):
        pass
    await ctx.send(embed=embed, delete_after=15)

@mail.command()
async def gonder(ctx, uye: discord.Member, *, mesaj: str):
    try:
        await ctx.message.delete()
    except (discord.Forbidden, discord.NotFound):
        pass

    if uye.id not in user_mails:
        user_mails[uye.id] = []
    
    sirket_maili = f"{ctx.author.display_name.lower().replace(' ', '.')}@gopostal.com"
    tam_gonderen = f"{ctx.author.display_name} <{sirket_maili}>"
    user_mails[uye.id].append({'gonderen': tam_gonderen, 'mesaj': mesaj, 'foto': ""})
    await ctx.send(f"✅ {uye.mention} adlı çalışana mail başarıyla iletildi! (Bu mesaj silinecektir)", delete_after=10)
    
    # Kullanıcıya özel mesaj (DM) - Komut ile atıldığında da şık gözükmesi için güncelledik
    try:
        embed = discord.Embed(title="🏢 GoPostal Şirket Maili", description=mesaj, color=discord.Color.blue())
        embed.set_author(name=f"Gönderen: {tam_gonderen}")
        embed.set_thumbnail(url="https://i.imgur.com/OXvf62G.png")
        embed.set_footer(text="GoPostal İletişim Sistemleri")
        await uye.send(embed=embed)
    except discord.Forbidden:
        pass

@mail.command()
async def oku(ctx):
    try:
        await ctx.message.delete()
    except (discord.Forbidden, discord.NotFound):
        pass

    mailler = user_mails.get(ctx.author.id, [])
    if not mailler:
        await ctx.send("📭 Gelen kutunuz şu an boş. (Bu mesaj silinecektir)", delete_after=10)
        return
    
    embed = discord.Embed(title=f"📧 {ctx.author.display_name} - Gelen Kutusu", color=discord.Color.green())
    embed.set_thumbnail(url="https://i.imgur.com/OXvf62G.png")
    for i, m in enumerate(mailler, 1):
        icerik = m['mesaj']
        if m.get('foto') and m['foto'].startswith("http"):
            icerik += f"\n\n📎 **Ek:** [Fotoğrafı Görüntüle]({m['foto']})"
        embed.add_field(name=f"Mail #{i} | Kimden: {m['gonderen']}", value=icerik, inline=False)
        
    try:
        await ctx.author.send(embed=embed)
        await ctx.send("✅ Mailleriniz size özel mesaj (DM) olarak gönderildi. (Bu mesaj silinecektir)", delete_after=10)
    except discord.Forbidden:
        await ctx.send("❌ Size özel mesaj gönderemiyorum. Lütfen gizlilik ayarlarınızı kontrol edin. (Bu mesaj silinecektir)", delete_after=10)

@mail.command()
async def temizle(ctx):
    try:
        await ctx.message.delete()
    except (discord.Forbidden, discord.NotFound):
        pass
    if ctx.author.id in user_mails:
        user_mails[ctx.author.id] = []
    await ctx.send("🗑️ Gelen kutunuz başarıyla temizlendi. (Bu mesaj silinecektir)", delete_after=10)

@mail.command()
async def toplu(ctx, *, mesaj: str):
    try:
        await ctx.message.delete()
    except (discord.Forbidden, discord.NotFound):
        pass

    sirket_maili = f"{ctx.author.display_name.lower().replace(' ', '.')}@gopostal.com"
    tam_gonderen = f"{ctx.author.display_name} (Toplu Duyuru) <{sirket_maili}>"
    
    gonderilen = 0
    calisan_rolleri = ["Şube Müdürü", "Lojistik Uzmanı", "Kargo Aracı Şoförü", "Motorlu Kurye"]
    
    for uye in ctx.guild.members:
        if uye.bot: continue
        uye_rolleri = [r.name for r in uye.roles]
        
        if any(r in calisan_rolleri for r in uye_rolleri) and not ("LEGAL FM" in uye_rolleri or "Müşteri" in uye_rolleri):
            if uye.id not in user_mails:
                user_mails[uye.id] = []
            
            user_mails[uye.id].append({'gonderen': tam_gonderen, 'mesaj': mesaj, 'foto': ""})
            gonderilen += 1
            
            try:
                embed = discord.Embed(title="📢 GoPostal Şirket Duyurusu (Toplu Mail)", description=mesaj, color=discord.Color.red())
                embed.set_author(name=f"Gönderen: {tam_gonderen}")
                embed.set_thumbnail(url="https://i.imgur.com/OXvf62G.png")
                embed.set_footer(text="GoPostal İletişim Sistemleri")
                await uye.send(embed=embed)
            except discord.Forbidden:
                pass

    await ctx.send(f"✅ Toplu mail **{gonderilen}** çalışana başarıyla iletildi! (Bu mesaj silinecektir)", delete_after=10)

@bot.group(invoke_without_command=True)
async def siparis(ctx):
    embed = discord.Embed(title="📦 GoPostal GTA 5 Sipariş Takip Sistemi", color=discord.Color.orange())
    embed.add_field(name="Nasıl Kullanılır?", value="Bu sistem GTA 5 haritası üzerindeki kargoların durumunu ve anlık konumunu takip etmek için kullanılır.", inline=False)
    embed.add_field(name="Komutlar", value="`!siparis olustur <Bölge veya X,Y> <Paket İçeriği>` - Yeni kargo kaydı açar.\n`!siparis guncelle <KargoID> <Konum veya X,Y> <Durum>` - Kargonun haritadaki yerini günceller.\n`!siparis takip <KargoID>` - Kargonun nerede olduğunu harita linkiyle gösterir.", inline=False)
    embed.set_thumbnail(url="https://i.imgur.com/OXvf62G.png")
    await ctx.send(embed=embed)

@siparis.command()
async def olustur(ctx, bolge: str, *, icerik: str):
    global order_counter
    order_counter += 1
    kargo_id = f"GP-{order_counter}"
    
    orders[kargo_id] = {
        "olusturan": ctx.author.display_name,
        "hedef_bolge": bolge, # Örn: Sandy Shores, Paleto Bay, Vespucci
        "icerik": icerik,
        "durum": "Paketleniyor",
        "konum": "GoPostal Merkez Depo"
    }
    
    url = harita_linki_olustur(bolge)
    await ctx.send(f"✅ Yeni kargo talebi oluşturuldu!\n**Kargo ID:** `{kargo_id}`\n**Teslimat Noktası:** {bolge} - Haritada Gör\n*Müşteriler bu ID ile kargolarını takip edebilir.*")

@siparis.command()
@commands.has_any_role("Şube Müdürü", "Lojistik Uzmanı", "Kargo Aracı Şoförü", "Motorlu Kurye")
async def guncelle(ctx, kargo_id: str, konum: str, *, durum: str):
    if kargo_id not in orders:
        await ctx.send("❌ Belirtilen ID'ye ait bir kargo bulunamadı.")
        return
    
    orders[kargo_id]["konum"] = konum
    orders[kargo_id]["durum"] = durum
    
    url = harita_linki_olustur(konum)
    await ctx.send(f"✅ `{kargo_id}` numaralı kargonun durumu güncellendi!\n📍 **Yeni Konum:** {konum} - Haritada Gör\n🚚 **Durum:** {durum}")

@siparis.command()
async def takip(ctx, kargo_id: str):
    if kargo_id not in orders:
        await ctx.send("❌ Belirtilen ID'ye ait bir kargo bulunamadı. Lütfen kargo numarasını kontrol edin (Örn: GP-1001).")
        return
    
    kargo = orders[kargo_id]
    bolge_url = harita_linki_olustur(kargo['hedef_bolge'])
    konum_url = harita_linki_olustur(kargo['konum'])

    embed = discord.Embed(title=f"📦 Kargo Takip: {kargo_id}", color=discord.Color.gold())
    embed.set_thumbnail(url="https://i.imgur.com/OXvf62G.png")
    embed.add_field(name="Paket İçeriği", value=kargo["icerik"], inline=False)
    embed.add_field(name="Hedef Teslimat Bölgesi", value=f"[{kargo['hedef_bolge']}]({bolge_url})", inline=True)
    embed.add_field(name="Anlık Harita Konumu", value=f"📍 [{kargo['konum']}]({konum_url})", inline=True)
    embed.add_field(name="Son Durum", value=f"🚚 {kargo['durum']}", inline=False)
    embed.set_footer(text=f"Talep Eden: {kargo['olusturan']}")
    
    await ctx.send(embed=embed)

@bot.command()
async def bakiye(ctx):
    miktar = balances.get(ctx.author.id, 0)
    embed = discord.Embed(title="💳 GoPostal Maaş Hesabı", description=f"Sayın **{ctx.author.display_name}**, teslimatlardan kazandığınız güncel bakiyeniz: **${miktar}**", color=discord.Color.green())
    embed.set_thumbnail(url="https://i.imgur.com/OXvf62G.png")
    await ctx.send(embed=embed)

@bot.command()
@commands.has_any_role("LEGAL FM", "Şube Müdürü")
async def legal(ctx, *, sozlesme: str = None):
    if not sozlesme:
        await ctx.send("❌ Lütfen yasal bildirim metnini girin. Örn: `!legal Sözleşme şartları...`")
        return
        
    embed = discord.Embed(title="📜 GoPostal Yasal Bildirim & Sözleşme", description=sozlesme, color=discord.Color.purple())
    embed.set_thumbnail(url="https://i.imgur.com/OXvf62G.png")
    embed.set_footer(text=f"Departman: LEGAL FM | İmza: {ctx.author.display_name}")
    
    try:
        await ctx.message.delete() # Komutu yazanın mesajını silip gizler, sadece şık Embed tablo kalır.
    except:
        pass
        
    await ctx.send(embed=embed)

@bot.command(aliases=['sonlandir'])
async def sonlandır(ctx):
    roller = [r.name for r in ctx.author.roles]
    yetkili = ["Şube Müdürü", "Lojistik Uzmanı", "Kargo Aracı Şoförü", "Motorlu Kurye", "LEGAL FM"]
    if not any(r in yetkili for r in roller) and not ctx.author.guild_permissions.administrator and ctx.author.name != "saintvor_":
        await ctx.send("❌ Bu işlem için şirket yetkiniz yok.", delete_after=5)
        return

    if not ctx.channel.name.startswith("kargo-"):
        await ctx.send("❌ Bu komut sadece müşteri kargo talebi kanallarında kullanılabilir.", delete_after=5)
        return

    kid = ctx.channel.name.replace("kargo-", "").upper()
    if kid in orders:
        orders[kid]["durum"] = "Teslim Edildi / Sonlandırıldı"

    arsiv_kat = discord.utils.get(ctx.guild.categories, name="📁 ARŞİVLENEN KARGOLAR") or await ctx.guild.create_category("📁 ARŞİVLENEN KARGOLAR")
    
    yeni_overwrites = {ctx.guild.default_role: discord.PermissionOverwrite(read_messages=False)}
    for r_name in yetkili:
        r = discord.utils.get(ctx.guild.roles, name=r_name)
        if r: yeni_overwrites[r] = discord.PermissionOverwrite(read_messages=True, send_messages=False)
    
    await ctx.channel.edit(category=arsiv_kat, overwrites=yeni_overwrites)
    await ctx.send(f"📦 **Bu kargo talebi {ctx.author.mention} tarafından sonlandırılmış ve arşive kaldırılmıştır.**")
import os
from dotenv import load_dotenv
load_dotenv()
TOKEN = os.getenv('DISCORD_TOKEN')

if not TOKEN:
    raise RuntimeError("DISCORD_TOKEN ortam değişkeni tanımlı değil.")

bot.run(TOKEN)