/* remove only the generated entries; keep later user edits */
call removeStartup
if result ~= 0 then exit 20
call removeMenu
exit result

removeStartup: procedure
file = 'S:User-Startup'
if ~open('input', file, 'r') then return 20
text = ''
skip = 0
do while ~eof('input')
    line = readln('input')
    if line = ';RGB2RTG - Added by Emu68 Hatcher - BEGIN' then skip = 1
    else if line = ';RGB2RTG - Added by Emu68 Hatcher - END' then skip = 0
    else if ~skip then text = text || line || '0a'x
end
call close 'input'
if skip then return 20
call replaceFile file, text
return result

removeMenu: procedure
file = 'S:ToolsDaemon.menu'
if ~open('input', file, 'r') then return 20
expected.1 = 'ITEM RGB2RTG'
expected.2 = 'SUB On/off...'
expected.3 = '(CLI) 8192 C:rgb2rtg ASK'
expected.4 = 'SUB RTG Image...'
expected.5 = '(CLI) 8192 C:rgb2rtg IMAGE'
expected.6 = 'SUB About...'
expected.7 = '(CLI) 8192 C:rgb2rtg ABOUT'
expected.8 = 'ITEMBAR'
text = ''
system = 0
do while ~eof('input')
    line = readln('input')
    if left(strip(line), 6) = 'TITLE ' then system = strip(line) = 'TITLE System'
    if system & strip(line) = expected.1 then do
        do i = 2 to 8
            if eof('input') then do
                call close 'input'
                return 20
            end
            if strip(readln('input')) ~= expected.i then do
                call close 'input'
                return 20
            end
        end
    end
    else text = text || line || '0a'x
end
call close 'input'
call replaceFile file, text
return result

replaceFile: procedure
parse arg file, text
if ~open('output', file || '.rgb2rtg-new', 'w') then return 20
call writech 'output', text
call close 'output'
address command 'Copy "' || file || '.rgb2rtg-new" "' || file || '" QUIET'
if rc ~= 0 then return 20
address command 'Delete "' || file || '.rgb2rtg-new" QUIET'
return 0
