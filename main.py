import json
import os
import re
import sqlite3
import textwrap
import zipfile
from urllib.parse import urlparse, parse_qs

import PIL
import qrcode
from PIL import Image, ImageFont, ImageDraw

import requests as requests
from PIL.Image import Resampling

head = {'X-API-KEY': os.environ['X-API-KEY']}


# TODO: Доделать
class BungieException(Exception):
    def __init__(self, error=None):
        super().__init__()


class Loadout:
    def __init__(self, dim_url):
        Loadout.update_manifest()
        self.dim_url = dim_url
        request = requests.get(dim_url)
        try:
            url = re.search(r'https://app.destinyitemmanager.com/loadouts?\S*?"', request.text)[0][:-1]
        except IndexError:
            raise ValueError('Ссылка на набор не является валидной!')
        parsed_url = urlparse(url)
        self.loadout_dict = json.loads(parse_qs(parsed_url.query)['loadout'][0])

        self.equipped = [Loadout.get_item_by_hash(item['hash'], 'DestinyInventoryItemDefinition')
                         for item in self.loadout_dict['equipped']]

        # Отбор экзотов
        self.exotic_armor = {'hash': None, 'displayProperties': {
            'icon': '/common/destiny2_content/icons/7d0f3ca8a415207368444765fb76b0b0.jpg'}}
        self.exotic_weapon = {'hash': None, 'displayProperties': {
            'icon': '/common/destiny2_content/icons/281035e8d4ec41fdf3363c7307e3cc0b.jpg'}}
        for item in self.equipped:
            if item['inventory']['tierTypeHash'] == 2759499571:
                if 1734090384 in item['traitHashes']:
                    self.exotic_armor = item
                if 4021177463 in item['traitHashes']:
                    self.exotic_weapon = item

        self.subclass_data = self.loadout_dict['equipped'][-1]
        for item in self.loadout_dict['equipped']:
            if set(self.get_item_by_hash(item['hash'], 'DestinyInventoryItemDefinition').get('traitHashes')) & {
                1629967954, 951237008}:
                self.subclass_data = item
                break

        self.subclass_definition = Loadout.get_item_by_hash(self.subclass_data['hash'],
                                                            'DestinyInventoryItemDefinition')
        if not set(self.subclass_definition['traitHashes']) & {1629967954, 951237008}:
            raise ValueError('Не обнаружен подкласс в билде')

        self.armor_mods = {}
        self.subclass = {'ultimate': None,
                         'aspects': [],
                         'fragments': [],
                         'abilities': []}
        self.classify_armor_mods()
        self.classify_subclass()

    def classify_armor_mods(self):
        mods = self.loadout_dict['parameters']['mods']
        # Второстепеннные модификаторы характеристик
        # Основные модификаторы характеристик
        mods_blaclist = [3682186345, 3699676109, 204137529, 1227870362, 2623485440, 555005975,
                         2645858828, 3253038666, 3355995799, 4048838440, 2850583378, 3961599962]
        for modifier in mods_blaclist:
            while modifier in mods:
                mods.remove(modifier)
        # mod['plug']['plugCategoryHash']
        armor_slots = [2912171003,  # Голова
                       3422420680,  # Руки
                       1526202480,  # Тело
                       2111701510,  # Ноги
                       912441879,  # Классовый предмет
                       0,  # Прочие модификаторы (без привязки к слоту)
                       ]
        self.armor_mods = {
            key: {
                'capacity': 0,
                'energy_type': 1198124803,
                'mods': [],
                'special_mod': None
            } for key in armor_slots
        }

        modifiers = [self.get_item_by_hash(mod, 'DestinyInventoryItemDefinition') for mod in mods]
        modifiers.sort(key=lambda mod: mod['plug']['energyCost']['energyTypeHash'] == 1198124803)
        for modifier in modifiers:
            mod_info = modifier['plug']
            modifier_plug_hash = mod_info['plugCategoryHash']
            modifier_energy_type = mod_info['energyCost']['energyTypeHash']
            try:
                self.armor_mods[modifier_plug_hash]['mods'].append(modifier['hash'])
                self.armor_mods[modifier_plug_hash]['capacity'] += mod_info['energyCost']['energyCost']
                if self.armor_mods[modifier_plug_hash][
                    'energy_type'] == 1198124803 and modifier_energy_type != 1198124803:
                    self.armor_mods[modifier_plug_hash]['energy_type'] = modifier_energy_type
            except KeyError:
                self.armor_mods[0]['mods'].append(modifier['hash'])

        for modifier in self.armor_mods[0]['mods']:
            mod_info = self.get_item_by_hash(modifier, 'DestinyInventoryItemDefinition')
            mod_info = mod_info['plug']
            modifier_energy = mod_info['energyCost']['energyTypeHash']
            modifier_cost = mod_info['energyCost']['energyCost']

            sorted_list = list((self.armor_mods[armor]['capacity'],
                                self.armor_mods[armor]['energy_type'] == 1198124803, armor)
                               for armor in self.armor_mods)
            sorted_list.sort(key=lambda x: (x[0], not x[1]), reverse=True)

            for armor_obj in sorted_list:
                armor = armor_obj[-1]
                if armor == 0:
                    continue
                if self.armor_mods[armor]['special_mod']:
                    continue
                if self.armor_mods[armor]['energy_type'] == modifier_energy or self.armor_mods[armor][
                    'energy_type'] == 1198124803 or modifier_energy == 1198124803:
                    if self.armor_mods[armor]['capacity'] + modifier_cost <= 10:
                        if modifier_energy != 1198124803:
                            self.armor_mods[armor]['energy_type'] = modifier_energy
                        self.armor_mods[armor]['capacity'] += modifier_cost
                        self.armor_mods[armor]['special_mod'] = modifier
                        break

        self.armor_mods.pop(0)
        for armor in self.armor_mods:
            if self.armor_mods[armor]['special_mod']:
                self.armor_mods[armor]['mods'].append(self.armor_mods[armor]['special_mod'])
            self.armor_mods[armor].pop('special_mod')

    def classify_subclass(self):
        if not self.subclass_data.get('socketOverrides'):
            raise ValueError('Не обнаружен подкласс 3.0')
        for socket in self.subclass_data['socketOverrides']:
            socket_definition = self.get_item_by_hash(self.subclass_data['socketOverrides'][socket],
                                                      'DestinyInventoryItemDefinition')
            if 'fragments' in socket_definition['plug']['plugCategoryIdentifier']:
                self.subclass['fragments'].append(self.subclass_data['socketOverrides'][socket])
            elif 'trinkets' in socket_definition['plug']['plugCategoryIdentifier']:
                self.subclass['fragments'].append(self.subclass_data['socketOverrides'][socket])

            elif 'aspects' in socket_definition['plug']['plugCategoryIdentifier']:
                self.subclass['aspects'].append(self.subclass_data['socketOverrides'][socket])
            elif 'totems' in socket_definition['plug']['plugCategoryIdentifier']:
                self.subclass['aspects'].append(self.subclass_data['socketOverrides'][socket])

            elif 'supers' in socket_definition['plug']['plugCategoryIdentifier']:
                self.subclass['ultimate'] = self.subclass_data['socketOverrides'][socket]

            else:
                self.subclass['abilities'].append(self.subclass_data['socketOverrides'][socket])

            if not self.subclass['ultimate']:
                ultimate = {0: 2021620139,
                            1: 2625980631,
                            2: 3683904166}
                self.subclass['ultimate'] = ultimate[self.loadout_dict['classType']]

    def __repr__(self):
        return str(self.loadout_dict)

    def generate_picture(self):
        background = Image.open('assets/background.png')

        x, y = 1150, 397
        delta_x = 96 + 51
        delta_y = 96 + 138
        for armor in self.armor_mods:
            for mod in self.armor_mods[armor]['mods']:
                mod_image = self.render_mod(mod)
                background.paste(mod_image, (x, y))
                x += 111
            x -= len(self.armor_mods[armor]['mods']) * 111
            y += 112

        x, y = 168, 1141
        for aspect in self.subclass['aspects']:
            aspect_image = self.render_mod(aspect)
            background.paste(aspect_image, (x, y))
            x += delta_x
        x -= len(self.subclass['aspects']) * delta_x
        y += delta_y

        for fragment in self.subclass['fragments']:
            aspect_image = self.render_mod(fragment, render_cost=False)
            background.paste(aspect_image, (x, y))
            x += delta_x
        x -= len(self.subclass['fragments']) * delta_x
        y += delta_y

        for ability in self.subclass['abilities']:
            aspect_image = self.render_mod(ability)
            background.paste(aspect_image, (x, y))
            x += delta_x
        x -= len(self.subclass['abilities']) * delta_x
        y += delta_y

        x, y = 120, 388
        subclass_definition = self.get_item_by_hash(self.subclass_data['hash'], 'DestinyInventoryItemDefinition')
        subclass_screenshot = subclass_definition['screenshot']
        r = requests.get(f'https://www.bungie.net/{subclass_screenshot}', stream=True,
                         headers=head).raw
        subclass_image = Image.open(r).convert('RGBA')
        subclass_image = subclass_image.crop((365, 0, 1847, 1080))
        subclass_image.thumbnail((771, 563), Resampling.LANCZOS)
        background.paste(subclass_image, (x, y))
        subclass_name = subclass_definition['displayProperties']['name'].upper()
        ultimate_definition = self.get_item_by_hash(self.subclass['ultimate'], 'DestinyInventoryItemDefinition')
        ultimate_name = ultimate_definition['displayProperties']['name'].upper()
        ultimate_name = ultimate_name.replace('ТЕНЕВОЙ ВЫСТРЕЛ: ', '')
        ultimate_ico = ultimate_definition['displayProperties']['icon']
        r = requests.get(f'https://www.bungie.net/{ultimate_ico}', stream=True,
                         headers=head).raw
        ultimate_ico = Image.open(r).convert('RGBA')
        ultimate_ico.thumbnail((180, 180), Resampling.LANCZOS)
        background.paste(ultimate_ico, (680, 1067), mask=ultimate_ico)

        if textwrap.fill(subclass_name, 15).count('\n') > 1:
            subclass_name_len = len(subclass_name)
            while textwrap.fill(subclass_name, 15).count('\n') > 1:
                subclass_name_len -= 1
                subclass_name = subclass_name[:subclass_name_len]
            subclass_name = subclass_name[:len(subclass_name) - 3]
            subclass_name += '...'

        subclass_name_font = ImageFont.truetype('assets/fonts/arial_black.ttf',
                                                size=73)  # Обязательно с кирилицей
        x, y = 121, 99  # 83
        draw = ImageDraw.Draw(background)
        draw.text((x, y), textwrap.fill(subclass_name, 15), font=subclass_name_font)
        y += (textwrap.fill(subclass_name, 15).count('\n') + 1) * subclass_name_font.getsize(subclass_name)[1]

        if textwrap.fill(subclass_name, 25).count('\n') > 1:
            ultimate_name_len = len(subclass_name)
            while textwrap.fill(ultimate_name, 25).count('\n') > 1:
                ultimate_name_len -= 1
                ultimate_name = ultimate_name[:ultimate_name_len]
            ultimate_name = ultimate_name[:len(ultimate_name) - 3]
            ultimate_name += '...'

        ultimate_name_font = ImageFont.truetype('assets/fonts/arial_black.ttf',
                                                size=50)  # Обязательно с кирилицей

        x, y = 121, 304
        if textwrap.fill(ultimate_name, 15).count('\n') > 0:
            y -= ultimate_name_font.getsize(ultimate_name)[1]
            ultimate_name = textwrap.fill(ultimate_name, 15)

        draw = ImageDraw.Draw(background)
        draw.text((x, y), textwrap.fill(ultimate_name, 15), font=ultimate_name_font)
        y += (textwrap.fill(ultimate_name, 25).count('\n') + 1) * ultimate_name_font.getsize(ultimate_name)[1]

        x, y = 1050, 1155
        if self.exotic_armor['hash']:
            exotic_armor_icon = self.render_mod(self.exotic_armor['hash'], placeholder=False)
        else:
            exotic_armor_icon = self.render_mod(self.exotic_armor, placeholder=True)
        background.paste(exotic_armor_icon, (x, y))
        x += 96 + 30
        if self.exotic_weapon['hash']:
            exotic_weapon_icon = self.render_mod(self.exotic_weapon['hash'], placeholder=False)
        else:
            exotic_weapon_icon = self.render_mod(self.exotic_weapon, placeholder=True)
        background.paste(exotic_weapon_icon, (x, y))

        bar_code = qrcode.make(self.dim_url)
        background.paste(bar_code, (1051, 1387))
        return background

    @staticmethod
    def update_manifest():
        manifest_url = 'http://www.bungie.net/Platform/Destiny2/Manifest'
        r = requests.get(manifest_url, headers=head)
        try:
            manifest = r.json()
        except json.decoder.JSONDecodeError:
            raise BungieException(r)
        if manifest['ErrorCode'] != 1:
            raise BungieException(manifest['Message'])
        mani_v = manifest['Response']['version']
        try:
            file = open('manifest_version', 'r+')
        except FileNotFoundError:
            open('manifest_version', 'w').close()
            file = open('manifest_version', 'r+')
        v = file.read()
        file.close()
        if mani_v == v:
            return
        mani_url = 'http://www.bungie.net' + manifest['Response']['mobileWorldContentPaths']['ru']
        try:
            r = requests.get(mani_url, headers=head)
        except Exception as e:
            raise BungieException(e)
        with open("MANZIP", "wb") as archive:
            archive.write(r.content)
        with zipfile.ZipFile('MANZIP') as archive:
            name = archive.namelist()
            archive.extractall()
        try:
            os.remove('Manifest.db')
        except FileNotFoundError:
            pass
        os.rename(name[0], 'Manifest.db')
        file = open('manifest_version', 'w')
        file.write(mani_v)
        file.close()

    @staticmethod
    def get_item_by_hash(hash_id, definition):
        conn = sqlite3.connect('Manifest.db')
        cur = conn.cursor()
        try:
            cur.execute(f"SELECT json FROM {definition} WHERE id + 4294967296 = {hash_id} OR id = {hash_id}")
        except sqlite3.Error as e:
            raise ValueError()
        res = cur.fetchall()[0][0]
        res = json.loads(res)
        conn.close()
        return res

    def render_mod(self, itemHash, render_cost=True, placeholder=False):
        if not placeholder:
            item = self.get_item_by_hash(itemHash, 'DestinyInventoryItemDefinition')
        else:
            item = itemHash
        r = requests.get(f'https://www.bungie.net/{item["displayProperties"]["icon"]}', stream=True,
                         headers=head).raw
        image = Image.open(r).convert('RGBA')
        mod_background = Image.new('RGBA', (96, 96), color='#252525')
        mod_background.paste(image, mask=image)
        image = mod_background
        draw = ImageDraw.Draw(image)
        if render_cost:
            try:
                mod_energy_ico = self.get_item_by_hash(item['investmentStats'][0]['statTypeHash'],
                                                       'DestinyStatDefinition')
                r = requests.get(f'https://www.bungie.net/{mod_energy_ico["displayProperties"]["icon"]}',
                                 stream=True, headers=head).raw
                energy_mod = Image.open(r).convert('RGBA')
                image.paste(energy_mod, mask=energy_mod)
                mod_energy_cost = item['plug']['energyCost']['energyCost']
                draw.text((73, 6), str(mod_energy_cost),
                          font=ImageFont.truetype('assets/fonts/TrebuchetMS.ttf', size=20))
            except (IndexError, KeyError, PIL.UnidentifiedImageError):
                pass
        mod_image = Image.new('RGB', (100, 100), color='#d2d2d2')
        mod_image.paste(image, (2, 2))
        maxsize = (96, 96)
        mod_image.thumbnail(maxsize, Resampling.LANCZOS)
        return mod_image


if __name__ == '__main__':
    urls = ['https://dim.gg/l5oc76a/testExotic', 'https://dim.gg/ejcwjva/ArcTest', 'https://dim.gg/ldw5r6q/StasisTest',
            'https://dim.gg/mul352q/VoidTest', 'https://dim.gg/lmvmhyi/Farm-Duality',
            'https://dim.gg/q42oh4y/StasisTest', 'https://dim.gg/xyqwuxq/Iz-ekipirovannyh-11.06.2022-10:03:02',
            'https://dim.gg/byqpyiy/pvp',
            'https://dim.gg/a7h3hfq/StasisTest', 'https://dim.gg/3drn4uy/VoidTest', 'https://dim.gg/6pbixcy/SolarTest']

    l = Loadout('https://dim.gg/hp3yqai/Ohotnik-solar-3.0-s-mechom')
    l.generate_picture().save(f'results/{100}.png', format="png")
    exit(0)

    for i, url in enumerate(urls):
        try:
            l = Loadout(url)
            l.generate_picture().save(f'results/{i}.png', format="png")
            exit(0)
        except ValueError as e:
            print(e)
