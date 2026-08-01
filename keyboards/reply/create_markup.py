from telebot import types

def create_markup(buttons):
    """ Создает кнопки для ответа """

    markup = types.InlineKeyboardMarkup()
    for text, callback_data in buttons:
        markup.add(types.InlineKeyboardButton(text=text, callback_data=callback_data))
    return markup

def create_markup_with_url(buttons):
    markup = types.InlineKeyboardMarkup()
    for text, url, callback_data in buttons:
        button = types.InlineKeyboardButton(text=text, url=url, callback_data=callback_data)
        markup.add(button)
    # for text, url in buttons:
    #     markup.add(types.InlineKeyboardButton(text=text, url=url))
    return markup
