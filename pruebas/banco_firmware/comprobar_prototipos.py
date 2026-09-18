#!/usr/bin/env python3
"""Comprueba que un sketch .ino sobrevive al preprocesador del IDE de Arduino.

El IDE NO compila el .ino tal cual: genera un prototipo por cada funcion del
fichero y los inserta TODOS JUNTOS justo antes de la primera definicion de
funcion. Si un tipo propio (struct, enum, class) se declara despues de ese
punto y aparece en la firma de alguna funcion, el prototipo generado lo usa
antes de existir y el IDE falla con:

    error: variable or field 'X' declared void
    error: 'MiTipo' was not declared in this scope

...senalando la linea de la DEFINICION, que es donde el #line generado apunta,
asi que el mensaje despista bastante. Compilando el .ino como C++ normal (que
es lo que hace el banco de pruebas) el fallo NO aparece: solo sale en el IDE.

Regla, por tanto: todo tipo que se use en la firma de una funcion tiene que
estar declarado antes de la primera funcion del fichero.
"""
import re
import sys

DECL = re.compile(r'^\s*(?:typedef\s+)?(struct|class|enum(?:\s+class)?)\s+([A-Za-z_]\w*)')
# Definicion de funcion a nivel de fichero: empieza en la columna 0 y abre llave
FUNC = re.compile(r'^([A-Za-z_][\w\s\*&:<>,\[\]]*?)\b([A-Za-z_]\w*)\s*\(([^;]*)\)\s*(?:const\s*)?\{')
NO_ES_FUNCION = ('if', 'for', 'while', 'switch', 'else', 'return', 'do',
                 'struct', 'class', 'enum', 'union', 'typedef', 'extern')


def revisar(ruta):
    lineas = open(ruta, encoding='utf-8', errors='replace').read().split('\n')

    tipos = {}                       # nombre -> linea donde se declara
    for n, l in enumerate(lineas, 1):
        m = DECL.match(l)
        if m and m.group(2) not in tipos:
            tipos[m.group(2)] = n

    funciones = []                   # (linea, nombre, firma)
    for n, l in enumerate(lineas, 1):
        m = FUNC.match(l)
        if not m:
            continue
        if m.group(1).split()[0] in NO_ES_FUNCION or m.group(2) in NO_ES_FUNCION:
            continue
        funciones.append((n, m.group(2), m.group(1) + ' ' + m.group(3)))
    if not funciones:
        print(f'{ruta}: no encuentro ninguna funcion; revisa el patron')
        return 1

    corte = funciones[0][0]          # donde el IDE insertara los prototipos
    fallos = []
    for n, nombre, firma in funciones:
        for tipo, declarada in tipos.items():
            if declarada > corte and re.search(r'\b%s\b' % re.escape(tipo), firma):
                fallos.append((n, nombre, tipo, declarada))

    if fallos:
        print(f'{ruta}: FALLO')
        print(f'  El IDE insertara los prototipos en la linea {corte} '
              f'(primera funcion: {funciones[0][1]}).')
        for n, nombre, tipo, declarada in fallos:
            print(f'  - linea {n}: {nombre}() usa "{tipo}", declarado en la linea '
                  f'{declarada}, DESPUES del corte')
        print('  Solucion: mover la declaracion del tipo antes de la primera funcion.')
        return 1

    print(f'{ruta}: OK ({len(funciones)} funciones, {len(tipos)} tipos, '
          f'corte en la linea {corte})')
    return 0


if __name__ == '__main__':
    sys.exit(max(revisar(r) for r in sys.argv[1:]) if len(sys.argv) > 1 else 2)
