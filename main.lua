io.stdout:setvbuf("no")  -- Enable console output

function love.load()
    print("Game started!")  -- This will appear in terminal
end

function love.draw()
    love.graphics.print("Hello //DIG.EXCAVATION!", 100, 100)
end

function love.keypressed(key)
    if key == "escape" then
        love.event.quit()
    end
end